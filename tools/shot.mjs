// Скриншот живого демо «Интервью → ТЗ» для README и портфолио Kwork.
// Запуск:
//   xvfb-run -a --server-args="-screen 0 1280x900x24" \
//     node tools/browser/browser.mjs --profile main --script=projects/interview-spec-bot/tools/shot.mjs
const OUT = process.env.SHOT_OUT || 'projects/kwork-offers/portfolio/interview_spec_chat.png';
const URL = process.env.SHOT_URL || 'http://127.0.0.1:8099/';

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForSelector('#i', { timeout: 15000 });
await page.waitForTimeout(2500); // интервью стартует запросом /api/session
await page.fill('#i', 'Телеграм-бот для записи клиентов в барбершоп, с выбором мастера и времени');
await page.click('#b');
await page.waitForFunction(() => document.body.innerText.includes('Для кого это'), { timeout: 15000 });
await page.waitForTimeout(400);
await page.screenshot({ path: OUT, fullPage: true });
const text = (await page.innerText('body')).replace(/\s+/g, ' ').slice(0, 400);
console.log('SHOT_OK', OUT);
console.log('BODY:', text);
