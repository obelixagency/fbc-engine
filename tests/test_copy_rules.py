from fbc.copy_rules import check_copy


def test_flags_for_kids():
    assert len(check_copy("Fun quiz for kids")) == 1


def test_allows_family_hook():
    assert check_copy("Can you beat your kids?") == []


def test_flags_each_banned_phrase_case_insensitive():
    assert check_copy("TODDLER time") and check_copy("Kids' Quiz night") and check_copy("preschool fun")
