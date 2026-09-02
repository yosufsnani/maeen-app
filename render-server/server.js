// خادم Express يحوّل HTML شهادة الشكر لملف PDF حقيقي أفقي بجودة كاملة
// يستقبل: { html: "...", styles: "...", filename: "..." }
// يرجّع: PDF كملف ثنائي

const express = require('express');
const puppeteer = require('puppeteer');

const app = express();
app.use(express.json({ limit: '10mb' }));

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

app.post('/generate-cert-pdf', async (req, res) => {
  console.log('Received PDF request, html length:', req.body && req.body.html ? req.body.html.length : 'MISSING');

  const { html, styles, filename } = req.body || {};
  if (!html) {
    return res.status(400).send('Missing html field');
  }

  const fullHtml = `<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap" rel="stylesheet">
<style>
  html, body { margin:0; padding:0; }
  ${styles || ''}
  .cert-page{width:297mm; height:210mm; max-width:none; min-width:0; margin:0; padding:0; box-sizing:border-box;}
  .cert-card{width:100%; height:100%; min-height:0; aspect-ratio:auto; box-shadow:none; border-radius:0; display:flex; flex-direction:column;}
  .cert-header{height:156px; flex:none;}
  .cert-body-pad{flex:1; display:flex; flex-direction:column; justify-content:center; padding-bottom:18px;}
  .cert-sign-row{margin-top:auto;}
</style>
</head>
<body>${html}</body>
</html>`;

  let browser = null;
  try {
    console.log('Launching browser...');
    browser = await puppeteer.launch({
      headless: 'new',
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    });

    const page = await browser.newPage();
    await page.setViewport({ width: 1200, height: 850 });
    await page.emulateMediaType('screen');
    await page.setContent(fullHtml, { waitUntil: 'networkidle0', timeout: 15000 });
    await page.evaluateHandle('document.fonts.ready');

    console.log('Generating PDF...');
    const pdfBuffer = await page.pdf({
      format: 'A4',
      landscape: true,
      printBackground: true,
      margin: { top: '0mm', bottom: '0mm', left: '0mm', right: '0mm' },
    });

    console.log('PDF generated, size in bytes:', pdfBuffer.length);

    await browser.close();

    const safeName = (filename || 'شهادة شكر وتقدير').replace(/[\\/:*?"<>|]/g, '-');

    res.set({
      'Content-Type': 'application/pdf',
      'Content-Disposition': `attachment; filename="${encodeURIComponent(safeName)}.pdf"`,
    });
    res.send(Buffer.from(pdfBuffer));
    console.log('Response sent successfully.');
  } catch (err) {
    console.error('ERROR generating PDF:', err);
    if (browser) { try { await browser.close(); } catch (e) {} }
    res.status(500).json({ error: err && err.message ? err.message : 'فشل توليد الملف' });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
