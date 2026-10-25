"""Offline synthetic canaries only; never calls social APIs or uses real credentials."""
import json
import subprocess
from pathlib import Path

import pytest

from fbc import cli, log
from fbc.publish import _err, meta, youtube

CANARY = "synthetic-derived-token-not-in-env"


@pytest.mark.parametrize('message', [
    CANARY, f'https://graph.example/me?access_token={CANARY}',
    f'Authorization: Bearer {CANARY}', f'{{"refresh_token": "{CANARY}"}}',
    f'https://upload.example/video?signature={CANARY}',
])
def test_exception_summaries_never_serialize_payload(message):
    err = RuntimeError(message)
    assert _err(err) == 'RuntimeError'
    assert log.error_summary(err) == 'RuntimeError'


def test_log_reason_is_allowlisted(monkeypatch, capsys):
    monkeypatch.setenv('YT_REFRESH_TOKEN', 'synthetic-env-secret')
    log.safe('failed', reason=CANARY, status='synthetic-env-secret', error_type='RuntimeError')
    out = capsys.readouterr().out
    assert CANARY not in out and 'synthetic-env-secret' not in out
    assert log.safe_reason('invalid_grant') == 'invalid_grant'
    assert log.safe_reason('code_190') == 'code_190'


@pytest.mark.parametrize('module', [meta, youtube])
def test_http_error_does_not_echo_body(module):
    class Response:
        status_code = 400
        content = b'json'
        def json(self):
            return {'error': {'message': CANARY}}
    with pytest.raises(RuntimeError) as e:
        module._check(Response())
    assert CANARY not in str(e.value)
    assert '400' in str(e.value)


def test_generate_issue_omits_request_exception(tmp_path, monkeypatch):
    from fbc.store import ContentRepo
    calls = []
    repo = ContentRepo(tmp_path, "o/content", run_gh=lambda *args: calls.append(args) or "[]")
    import requests
    def fail(*a, **kw):
        raise requests.ConnectionError(f'https://example.test?access_token={CANARY}')
    monkeypatch.setattr(cli, 'build_batch', fail)
    with pytest.raises(requests.ConnectionError):
        cli.cmd_generate(repo, '2026-W45', True, cli.date(2026, 10, 25))
    assert CANARY not in repr(calls)


def test_auth_helper_refuses_actions_before_signin(monkeypatch, capsys):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    with pytest.raises(RuntimeError, match='token output is disabled'):
        cli.auth_youtube()
    assert not capsys.readouterr().out


def test_workflow_inputs_are_data_not_shell_code(tmp_path):
    root = Path(__file__).resolve().parents[1]
    for name in ('covers', 'generate', 'preview'):
        text = (root / '.github/workflows' / f'{name}.yml').read_text()
        # No raw expression interpolation in any run command (env entries are expected).
        for line in text.splitlines():
            if 'run:' in line:
                assert '${{ inputs.' not in line
    malicious = '$(touch ' + str(tmp_path / 'injected') + '); echo token'
    result = subprocess.run(['bash', '-c', 'args=(--week "$GENERATE_WEEK"); printf "%s" "${args[1]}"'],
                            env={'GENERATE_WEEK': malicious}, capture_output=True, text=True, check=True)
    assert result.stdout == malicious and not (tmp_path / 'injected').exists()


def test_media_host_rejects_diagnostic_files(tmp_path):
    from fbc.publish.media_host import push_public
    diagnostic = tmp_path / 'error.json'
    diagnostic.write_text(CANARY)
    with pytest.raises(ValueError):
        push_public(tmp_path, diagnostic, cli.date(2026, 10, 25))
    assert len(list(tmp_path.iterdir())) == 1


def test_publish_queue_and_logs_omit_derived_token(monkeypatch, tmp_path, capsys):
    from fbc import publish
    def fail(*a, **kw):
        raise RuntimeError(f'https://example.test/upload?access_token={CANARY}')
    monkeypatch.setattr(youtube, 'access_token', fail)
    monkeypatch.setattr(meta, 'fb_reel', fail)
    monkeypatch.setattr(publish.media_host, 'push_public', fail)
    entry = {'id': 's1', 'status': 'produced', 'platforms': {
        p: {'status': 'pending', 'ref': None, 'error': None, 'attempts': 0}
        for p in ('youtube', 'facebook', 'instagram')}}
    result = publish.publish_item(entry, {'title': 't', 'description': 'd', 'is_short': True},
                                  tmp_path / 'v.mp4', tmp_path, cli.date(2026, 10, 25))
    assert CANARY not in json.dumps(result) + capsys.readouterr().out
    assert all(p['status'] == 'failed' and p['attempts'] == 1 for p in result['platforms'].values())


def test_public_media_commit_only_includes_selected_paths(monkeypatch, tmp_path):
    from fbc.publish import media_host
    calls = []
    monkeypatch.setattr(media_host, '_git', lambda repo, *args: calls.append(args))
    class Status:
        stdout = 'A media.mp4'
    monkeypatch.setattr(media_host.subprocess, 'run', lambda *a, **k: Status())
    media_host._commit_push(tmp_path, 'media', ['media.mp4'])
    assert calls[0] == ('add', '--', 'media.mp4')
    commit = next(c for c in calls if 'commit' in c)
    assert commit[-2:] == ('--', 'media.mp4')
