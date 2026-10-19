# تشغيل Family Brain Clash — خطوة بخطوة

السيستم كله شغال على GitHub Actions ومن غير تكلفة. فيه 3 ريبوهات:

| الريبو | النوع | فيه إيه |
|---|---|---|
| `obelixagency/fbc-engine` | Public | الكود والـ workflows، ومفيهوش أي محتوى |
| `obelixagency/fbc-content` | Private | الأسئلة، والـ batches، وجدول النشر، والفيديوهات النهائية كـ Releases |
| `obelixagency/fbc-media` | Public + Pages | لينك public مؤقت عشان Instagram يقدر يسحب الفيديو، وبيتمسح بعد 14 يوم |

> **مهم:** ممنوع تكتب أي مفتاح في الشات أو في أي ملف. كل المفاتيح بتتحط في **GitHub Secrets** بس.

---

## 1) مفتاح Gemini (للأسئلة والفويس)
1. ادخل [Google AI Studio](https://aistudio.google.com/apikey) ← **Create API key**. اعمل مشروع جديد مخصوص للقناة واسمه `family-brain-clash`.
2. في Google Cloud Console للمشروع ده، روح على **APIs & Services ← Generative Language API ← Quotas** وحط حد يومي للاستخدام، عشان لو المفتاح اتسرب الضرر يبقى محدود.
3. احفظ المفتاح عشان هتحطه في الخطوة 6 كـ `GEMINI_API_KEY`.

## 2) مفتاح Pexels (للفيديوهات)
اعمل حساب مجاني على [pexels.com/api](https://www.pexels.com/api/) ← **Your API Key**. ده هيبقى `PEXELS_API_KEY`.

## 3) YouTube API
1. في نفس مشروع Google Cloud، فعّل الاتنين دول: **YouTube Data API v3** و **YouTube Analytics API**.
2. **OAuth consent screen**: اختار External، واسم التطبيق `Family Brain Clash Automation`، وضيف إيميلك كـ Test user، وبعدين اعمل **Publish app**.
3. **Credentials ← Create OAuth client ID ← Desktop app**. ده بيطلّعلك `YT_CLIENT_ID` و `YT_CLIENT_SECRET`.
4. عشان تطلّع الـ Refresh token، شغّل الأمر ده **على جهازك** (في Claude Code أو أي Terminal):
   ```bash
   git clone https://github.com/obelixagency/fbc-engine && cd fbc-engine && pip install -e .
   YT_CLIENT_ID=... YT_CLIENT_SECRET=... python -m fbc.cli auth-youtube
   ```
   هيفتحلك صفحة جوجل. ادخل بحساب **قناة Family Brain Clash** واضغط Allow. الأمر هيطبع `YT_REFRESH_TOKEN`، انسخه وحطه في Secrets، وبعدها امسحه من الـ Terminal.
5. **قدّم على الـ API audit** من [فورم YouTube API Services](https://support.google.com/youtube/contact/yt_api_form). لحد ما الطلب يتقبل، YouTube هيحوّل كل فيديو بيترفع من الـ API إلى **Private**، وإنت هتنشره بإيدك من YouTube Studio (موضّح في الخطوة 8).
6. وثّق القناة برقم الموبايل: **YouTube Studio ← Settings ← Channel ← Feature eligibility ← Intermediate features**. من غير التوثيق مش هتقدر ترفع thumbnails مخصوصة.

## 4) Facebook و Instagram
1. في [Meta for Developers](https://developers.facebook.com/apps) استخدم تطبيق "Unshaken Automation" الموجود، أو اعمل تطبيق جديد Business.
2. اربط صفحة Family Brain Clash وحساب Instagram بتاعها (لازم يبقى Professional ومربوط بالصفحة).
3. من **Graph API Explorer** اعمل Page access token طويل المدى، بالصلاحيات دي:
   `pages_manage_posts, pages_read_engagement, pages_show_list, publish_video, instagram_basic, instagram_content_publish, instagram_manage_comments, instagram_manage_insights`
4. الناتج هيبقى 3 قيم: `FB_PAGE_ID` و `FB_PAGE_TOKEN` و `IG_USER_ID` (ده الـ Instagram business account id المربوط بالصفحة).

## 5) توكن GitHub
روح على **GitHub ← Settings ← Developer settings ← Fine-grained tokens ← Generate**:
- **Resource owner:** `obelixagency`
- **Repositories:** `fbc-content` و `fbc-media`
- **Permissions:** Contents: Read and write، و Pull requests: Read and write، و Issues: Read and write

ده هيبقى `CONTENT_REPO_TOKEN`.

## 6) حط كل حاجة في fbc-engine
**fbc-engine ← Settings ← Secrets and variables ← Actions**

**في تاب Secrets:**
`GEMINI_API_KEY`، `PEXELS_API_KEY`، `YT_CLIENT_ID`، `YT_CLIENT_SECRET`، `YT_REFRESH_TOKEN`، `FB_PAGE_ID`، `FB_PAGE_TOKEN`، `IG_USER_ID`، `CONTENT_REPO_TOKEN`

**في تاب Variables:**
`GEMINI_TEXT_MODEL`، وقيمته اسم موديل Gemini للنصوص اللي متاح في الـ free tier بتاعك. الاسم موجود في AI Studio.

## 7) التجربة الأولى
1. **Actions ← Generate weekly batch ← Run workflow**، وعلّم على **dry run** في الأول. لو اشتغل من غير أخطاء، شغّله تاني من غير dry run.
2. هيتفتح **Pull Request** في `fbc-content`. راجع الأسئلة من تطبيق GitHub على الموبايل، وعدّل `batch.json` لو فيه حاجة غلط، وبعدين اعمل **Merge**.
3. الـ workflow اللي اسمه **Tick** شغال على طول: بيعمل دورة كل 10 دقايق لمدة حوالي 5 ساعات، وبعدها بيشغّل نفسه تاني. بيعمل الفويس ويرندر الفيديوهات اللي ميعادها خلال 48 ساعة، وبينشر اللي جه ميعاده.

## 8) الشغل الأسبوعي (حوالي 20 دقيقة)
- **الجمعة:** هيوصلك PR جديد. راجعه واعمله Merge. لو لسه ما عملتش Merge لحد الأحد الساعة 8 بالليل، هيوصلك تذكير.
- **مرة في الأسبوع في YouTube Studio:**
  - اربط الشورتس بالفيديو الطويل من خلال **Related video**.
  - شغّل **Test & Compare** على الـ 3 thumbnails بتوع الفيديو الطويل. هتلاقيهم في Release الأسبوع جوه `fbc-content`.
  - لحد ما الـ audit يتقبل، انشر الفيديوهات اللي حالتها Private بإيدك.
- **لو حصلت مشكلة:** هيتفتح **Issue** في `fbc-content` (والإشعار بيوصلك على الموبايل) ومكتوب فيه السبب.

## مواعيد النشر
- **الشورتس:** 4 مرات في اليوم: 7 الصبح، و12 الضهر، و4 العصر، و8 بالليل بتوقيت نيويورك (2 الضهر، و7 بالليل، و11 بالليل، و3 الفجر بتوقيت مصر).
- **الفيديو الطويل:** حلقتين في الأسبوع، الأربع والسبت الساعة 10 الصبح بتوقيت نيويورك (5 العصر بتوقيت مصر). يوتيوب بياخد الحلقة كاملة، وفيسبوك وانستجرام بياخدوا تيزر Reel فيه لينك الحلقة.
