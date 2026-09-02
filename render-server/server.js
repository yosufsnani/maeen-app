// خادم Express يحوّل HTML شهادة الشكر لملف PDF حقيقي أفقي بجودة كاملة
// يستقبل: { html: "...", styles: "...", filename: "..." }
// يرجّع: PDF كملف ثنائي

const express = require('express');
const puppeteer = require('puppeteer');

const app = express();
app.use(express.json({ limit: '10mb' }));

// ---- أبعاد البطاقة ----
// البطاقة مبنية بوحدات مرنة (cqw) نسبةً لعرض حاويتها (.cert-card)، فتُرسم مباشرة
// بمقاسها الحقيقي 297×210mm بدون أي تكبير لاحق. هذا يضمن أن خلفية الصورة المضمّنة
// في كل قالب (300dpi) تظهر بدقتها الكاملة في PDF، بدل أن تُرسم بمقاس صغير ثم تُمدَّد.
const PAGE_W_PX = (297 / 25.4) * 96;           // عرض A4 أفقي بالبكسل = 1122.52

app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

app.get('/', (req, res) => {
  res.send('Maeen PDF server is running.');
});

// يبني صفحة HTML كاملة جاهزة للطباعة. مُصدَّرة كي تُختبر بدون تشغيل الخادم.
function buildPrintHtml(html, styles) {
  return `<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap" rel="stylesheet">
<style>
  html, body { margin:0; padding:0; }
  ${styles || ''}

  /* ===== لا تضف أي قاعدة قبل هذا السطر: كل ما تحت يجب أن يتغلب على ستايلات الموقع ===== */

  /* مهم جداً: ستايلات الموقع تحتوي على @page{size:A4; margin:11mm} وكروم يعطيها
     الأولوية على خيارَي landscape و margin في page.pdf()، فتطلع الصفحة عمودية
     بهوامش وتنقص البطاقة من الجنب (يختفي أحد التوقيعين). هذه القاعدة تلغيها. */
  @page { size: 297mm 210mm; margin: 0; }

  html, body { width:297mm; height:210mm; margin:0; padding:0; overflow:hidden; background:#fff; }

  .cert-page{
    width:297mm; height:210mm;
    max-width:none; min-width:0; margin:0; padding:0;
    box-sizing:border-box;
    position:absolute; top:0; left:0;
    zoom:1 !important;                 /* تحييد zoom القادم من إعداد «حجم التقرير» */
  }
  .cert-card{
    width:100%; height:100%; min-height:0;
    aspect-ratio:auto; box-sizing:border-box;
    box-shadow:none; border-radius:0;
  }
</style>
</head>
<body>${html}</body>
</html>`;
}

// يفتح متصفحاً، يحمّل الشهادة، وينفّذ عليها المهمة المطلوبة. يضمن إغلاق المتصفح دائماً.
async function withCertPage(html, styles, deviceScaleFactor, task) {
  let browser = null;
  try {
    console.log('Launching browser...');
    browser = await puppeteer.launch({
      headless: 'new',
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    });

    const page = await browser.newPage();
    // مقاس الصفحة الأفقية بالبكسل تماماً، حتى تطابق لقطة الشاشة الـ PDF حرفياً
    await page.setViewport({
      width: Math.round(PAGE_W_PX),
      height: Math.round(PAGE_W_PX * 210 / 297),
      deviceScaleFactor: deviceScaleFactor || 1,
    });
    await page.emulateMediaType('screen');
    await page.setContent(buildPrintHtml(html, styles || ''), { waitUntil: 'networkidle0', timeout: 15000 });
    await page.evaluateHandle('document.fonts.ready');

    return await task(page);
  } finally {
    if (browser) { try { await browser.close(); } catch (e) {} }
  }
}

// ============ صورة PNG للشهادة ============
// يستخدمها زر الطباعة: الجوال لا يفتح نافذة طباعة لملف PDF، لكنه يفتحها لصفحة HTML.
// وبما أن الصور تُطبع دائماً بألوانها (بخلاف خلفيات CSS التي قد يطفئها المتصفح)،
// فطباعة صورة واحدة للشهادة تعطي نتيجة مطابقة على كل الأجهزة.
app.post('/generate-cert-image', async (req, res) => {
  const { html, styles, scale } = req.body || {};
  if (!html) return res.status(400).json({ error: 'Missing html field' });

  // 3 أضعاف ≈ 288dpi — دقة كافية للطباعة الورقية مع حجم ملف معقول
  const dsf = Math.min(Math.max(Number(scale) || 3, 1), 4);

  try {
    const png = await withCertPage(html, styles, dsf, (page) => page.screenshot({
      type: 'png',
      clip: { x: 0, y: 0, width: Math.round(PAGE_W_PX), height: Math.round(PAGE_W_PX * 210 / 297) },
    }));
    console.log('PNG generated, bytes:', png.length, 'dsf:', dsf);
    res.set({ 'Content-Type': 'image/png', 'Cache-Control': 'no-store' });
    res.send(Buffer.from(png));
  } catch (err) {
    console.error('ERROR generating image:', err);
    res.status(500).json({ error: err && err.message ? err.message : 'فشل توليد الصورة' });
  }
});

app.post('/generate-cert-pdf', async (req, res) => {
  console.log('Received PDF request, html length:', req.body && req.body.html ? req.body.html.length : 'MISSING');

  const { html, styles, filename } = req.body || {};
  if (!html) {
    return res.status(400).send('Missing html field');
  }

  try {
    console.log('Generating PDF...');
    // أبعاد صريحة بدل format+landscape، مع preferCSSPageSize حتى تتطابق مع قاعدة
    // @page أعلاه أياً كان أيهما يفوز في كروم — النتيجة واحدة في الحالتين.
    const pdfBuffer = await withCertPage(html, styles, 1, (page) => page.pdf({
      width: '297mm',
      height: '210mm',
      printBackground: true,
      preferCSSPageSize: true,
      pageRanges: '1',
      margin: { top: '0mm', bottom: '0mm', left: '0mm', right: '0mm' },
    }));

    console.log('PDF generated, size in bytes:', pdfBuffer.length);

    const safeName = (filename || 'شهادة شكر وتقدير').replace(/[\\/:*?"<>|]/g, '-');

    res.set({
      'Content-Type': 'application/pdf',
      'Content-Disposition': `attachment; filename="${encodeURIComponent(safeName)}.pdf"`,
    });
    res.send(Buffer.from(pdfBuffer));
    console.log('Response sent successfully.');
  } catch (err) {
    console.error('ERROR generating PDF:', err);
    res.status(500).json({ error: err && err.message ? err.message : 'فشل توليد الملف' });
  }
});

const PORT = process.env.PORT || 3000;
if (require.main === module) {
  app.listen(PORT, () => {
    console.log(`Server running on port ${PORT}`);
  });
}

module.exports = { app, buildPrintHtml };
