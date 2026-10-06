// Скриншот отрендеренного PDF-ТЗ (Chromium-вьюер) — для портфолио Kwork.
// Запуск:
//   xvfb-run -a --server-args="-screen 0 1400x1000x24" \
//     node tools/browser/browser.mjs --profile main --script=projects/interview-spec-bot/tools/shot_pdf.mjs
const SRC = process.env.PDF_SRC || '/tmp/spec.pdf';
const OUT = process.env.PDF_OUT || 'projects/kwork-offers/portfolio/interview_spec_pdf_1200.png';

await page.goto('file://' + SRC, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(6000); // вьюер рисует асинхронно
await page.screenshot({ path: OUT, fullPage: false });
const info = await page.evaluate(() => ({ url: location.href, title: document.title, body: (document.body.innerText || '').slice(0, 80) }));
console.log('PDF_SHOT', JSON.stringify(info));
