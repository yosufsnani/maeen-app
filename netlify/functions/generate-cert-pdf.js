// Netlify Function: يحوّل HTML شهادة الشكر لملف PDF حقيقي أفقي بجودة كاملة
// يستقبل: { html: "...", styles: "...", filename: "..." }
// يرجّع: PDF كملف ثنائي (base64) بترويسة تنزيل مباشر

const puppeteer = require('puppeteer-core');

exports.handler = async (event) => {
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };

  if (event.httpMethod === 'OPTIONS') {
    return { statusCode: 204, headers: corsHeaders, body: '' };
  }

  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, headers: corsHeaders, body: 'Method Not Allowed' };
  }

  const chromium = (await import('@sparticuz/chromium')).default;

  let payload;
  try {
    payload = JSON.parse(event.body || '{}');
  } catch (e) {
    return { statusCode: 400, headers: corsHeaders, body: 'Invalid JSON' };
  }

  const { html, styles, filename } = payload;
  if (!html) {
    return { statusCode: 400, headers: corsHeaders, body: 'Missing html field' };
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
    browser = await puppeteer.launch({
      args: chromium.args,
      defaultViewport: { width: 1200, height: 850 },
      executablePath: await chromium.executablePath(),
      headless: chromium.headless,
    });

    const page = await browser.newPage();
    await page.setContent(fullHtml, { waitUntil: 'networkidle0', timeout: 8000 });
    await page.evaluateHandle('document.fonts.ready');

    const pdfBuffer = await page.pdf({
      format: 'A4',
      landscape: true,
      printBackground: true,
      margin: { top: '0mm', bottom: '0mm', left: '0mm', right: '0mm' },
    });

    await browser.close();

    const safeName = (filename || 'شهادة شكر وتقدير').replace(/[\\/:*?"<>|]/g, '-');

    return {
      statusCode: 200,
      headers: {
        ...corsHeaders,
        'Content-Type': 'application/pdf',
        'Content-Disposition': `attachment; filename="${encodeURIComponent(safeName)}.pdf"`,
      },
      body: pdfBuffer.toString('base64'),
      isBase64Encoded: true,
    };
  } catch (err) {
    if (browser) { try { await browser.close(); } catch (e) {} }
    return {
      statusCode: 500,
      headers: corsHeaders,
      body: JSON.stringify({ error: err && err.message ? err.message : 'فشل توليد الملف' }),
    };
  }
};
