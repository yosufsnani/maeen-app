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
