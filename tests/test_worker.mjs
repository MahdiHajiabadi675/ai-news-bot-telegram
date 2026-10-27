/**
 * تست Worker (Node ≥ 20) — بدون شبکه و بدون Cloudflare
 * اجرا:  node tests/test_worker.mjs
 */
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const workerPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../worker/index.js');
const worker = (await import(pathToFileURL(workerPath).href)).default;

const ADMIN = 42;
let passed = 0;
const check = (label, cond) => { assert.ok(cond, `FAILED: ${label}`); passed++; console.log(`  ok - ${label}`); };

// ── KV در حافظه + fetch جعلی ──
const store = new Map();
const KV = {
  async get(k) { return store.has(k) ? store.get(k) : null; },
  async put(k, v) { store.set(k, v); },
};
const calls = [];
globalThis.fetch = async (url, opts) => {
  const body = opts?.body ? JSON.parse(opts.body) : {};
  calls.push({ method: String(url).split('/').pop(), body });
  return { json: async () => ({ ok: true }) };
};
const env = { BOT_TOKEN: 't', CHANNEL_ID: '@chan', ADMIN_ID: String(ADMIN), KV };
const set = (k, v) => store.set(k, JSON.stringify(v));
const get = (k) => (store.has(k) ? JSON.parse(store.get(k)) : null);
const last = (method) => [...calls].reverse().find(c => c.method === method);

async function send(update) {
  calls.length = 0;
  const res = await worker.fetch(new Request('https://w.example/webhook', {
    method: 'POST', body: JSON.stringify(update),
  }), env);
  assert.equal(await res.text(), 'OK');
}
const cb = (data, from = ADMIN) => ({ callback_query: { id: 'cb1', data, from: { id: from } } });
const msg = (text) => ({ message: { text, chat: { id: ADMIN } } });

const item = (id, extra = {}) => ({
  id, source: 'Wired AI', source_prior: 3, title_fa: 'عنوان', summary_fa: 'خلاصه',
  title: 'Original title', summary: 'Original summary', lang: 'en', translated: true,
  link: 'http://x', hashtags: ['AI'], importance: 'medium', confidence: 'likely', ...extra,
});

console.log('Clear queue:');
set('pending', [item('p1'), item('p2')]);
set('approved', [item('a1')]);
await send(cb('clearq'));
let confirm = last('sendMessage');
const datas = confirm.body.reply_markup.inline_keyboard.flat().map(b => b.callback_data);
check('تایید پاک‌کردن: سه گزینه', JSON.stringify(datas) === JSON.stringify(['clrp', 'clra', 'clrx']));
check('تا تایید نشود چیزی پاک نمی‌شود', get('pending').length === 2);

await send(cb('clrp', 999));
check('غیرادمین نمی‌تواند صف را پاک کند', get('pending').length === 2);

await send(cb('clrx'));
check('انصراف چیزی را پاک نمی‌کند', get('pending').length === 2 && get('approved').length === 1);

await send(cb('clrp'));
check('«فقط صف تایید» approved را دست نمی‌زند', get('pending').length === 0 && get('approved').length === 1);
check('پاک‌کردن در آمار منابع ثبت نمی‌شود', get('source_stats') === null);

set('pending', [item('p1')]);
await send(cb('clra'));
check('«همه» هر دو صف را پاک می‌کند', get('pending').length === 0 && get('approved').length === 0);

set('pending', [item('p1')]);
await send(msg('/clear'));
check('دستور /clear تایید می‌خواهد', last('sendMessage').body.reply_markup.inline_keyboard.length >= 2);
set('pending', []); set('approved', []);
await send(msg('/clear'));
check('صف خالی → پیام «خالی است»', last('sendMessage').body.text.includes('خالی'));

console.log('Dynamic trust:');
set('pending', [item('r1')]);
await send(cb('reject:r1'));
check('رد ادمین در source_stats ثبت می‌شود', get('source_stats')['Wired AI'].neg === 1);

set('pending', [item('ok1')]); set('approved', []); set('published', []);
await send(cb('approve:ok1'));
check('تایید ادمین ثبت می‌شود', get('source_stats')['Wired AI'].pos === 1);
const pub = calls.find(c => c.method === 'sendMessage' && c.body.chat_id === '@chan');
check('تایید → انتشار فوری در کانال (باگ maxPosts رفع شده)', !!pub);
check('پیام کانال: متن اصلی زیر ترجمه', pub.body.text.indexOf('عنوان') < pub.body.text.indexOf('متن اصلی')
  && pub.body.text.includes('<blockquote expandable>') && pub.body.text.includes('Original summary'));

set('pending', [item('f1')]);
await send(cb('feature:f1'));
check('«ویژه» وزن ۱٫۵ دارد', Math.abs(get('source_stats')['Wired AI'].pos - 2.5) < 0.05);

set('source_stats', {
  'OpenAI Blog': { prior: 5, pos: 0, neg: 0 },
  'Wired AI': { prior: 3, pos: 20, neg: 0, updated: new Date().toISOString() },
});
await send(msg('/sources'));
const rep = last('sendMessage').body.text;
check('/sources: منبع تقویت‌شده خودکار می‌شود', /🤖.*Wired AI/.test(rep));
check('/sources: منبع بدون شواهد = پیش‌فرض اولیه', /OpenAI Blog: 5 ← <b>5\.0<\/b>/.test(rep));

console.log('Feed health:');
set('feed_health', {
  Good: { ok: true, items: 8, new: 2, filtered: 1, filtered_titles: ['Phone review'], checked: '2026-10-25T10:00:00Z', fails: 0 },
  Broken: { ok: false, items: 0, new: 0, err: 'HTTPError: 404', fails: 5, checked: '2026-10-25T10:00:00Z' },
});
await send(msg('/feeds'));
const fr = last('sendMessage').body.text;
check('/feeds: فید خراب با علت و شمارنده', /❌ <b>Broken<\/b> — HTTPError: 404 \(پیاپی: 5\)/.test(fr));
check('/feeds: فید سالم با آمار', fr.includes('✅ Good: 8 آیتم، 2 جدید'));
check('/feeds: نمونهٔ ردشده‌ها', fr.includes('🚫 Phone review'));
set('feed_health', {});
await send(msg('/feeds'));
check('/feeds خالی', last('sendMessage').body.text.includes('هنوز'));

console.log('Run log:');
set('run_log', [
  { t: '2026-10-25T06:17:00Z', collected: true, feeds_ok: 22, feeds_total: 26, fetched: 6, analyzed: 5,
    auto: 3, pending: 2, low_score: 0, rejected_keyword: 1, published: 3, queue_pending: 2, queue_approved: 0 },
  { t: '2026-10-25T05:17:00Z', skipped: true, collected: false, published: 0 },
]);
await send(msg('/runs'));
const rn = last('sendMessage').body.text;
check('/runs: ساعت محلی (+۳:۳۰)', rn.includes('10-25 09:47'));
check('/runs: قیف', rn.includes('جدید 6 → تحلیل 5') && rn.includes('🤖3') && rn.includes('فید 22/26'));
check('/runs: اجرای بدون جمع‌آوری', rn.includes('جمع‌آوری نشد'));
set('run_log', []);
await send(msg('/runs'));
check('/runs خالی', last('sendMessage').body.text.includes('هنوز'));
set('feed_health', { Good: { ok: true, items: 5, new: 0, newest: new Date(Date.now() - 3 * 3600000).toISOString(), checked: '2026-10-25T10:00:00Z' } });
await send(msg('/feeds'));
check('/feeds: سن جدیدترین خبر فید', last('sendMessage').body.text.includes('آخرین خبر فید: 3 ساعت پیش'));

console.log('Review message:');
set('pending', [item('v1', { translated: false, source_trust: 3.4, corroborated_by: 2, score: 55 })]);
await send(msg('/pending'));
const rv = calls.filter(c => c.method === 'sendMessage').map(c => c.body).find(b => b.reply_markup);
check('پیام بررسی: برچسب ترجمه نشده', rv.text.includes('ترجمه نشده'));
check('پیام بررسی: اعتبار پویا + تایید چندمنبعی', rv.text.includes('3.4/5') && rv.text.includes('2 منبع'));
check('پیام بررسی: دکمهٔ پاک‌کردن کل صف',
  rv.reply_markup.inline_keyboard.flat().some(b => b.callback_data === 'clearq'));

console.log('Channel format:');
set('pending', [item('u1', { translated: false, title_fa: 'Original title', summary_fa: 'Original summary' })]);
set('approved', []); set('published', []);
await send(cb('approve:u1'));
const pub2 = calls.find(c => c.method === 'sendMessage' && c.body.chat_id === '@chan');
check('ترجمه‌نشده → برچسب و بدون بلوک تکراری',
  pub2.body.text.includes('ترجمه نشده') && !pub2.body.text.includes('متن اصلی</b>'));

set('pending', [item('fa1', { lang: 'fa' })]);
set('approved', []); set('published', []);
await send(cb('approve:fa1'));
const pub3 = calls.find(c => c.method === 'sendMessage' && c.body.chat_id === '@chan');
check('منبع فارسی → بدون بلوک متن اصلی', !pub3.body.text.includes('متن اصلی'));

set('pending', [item('big1', { summary: 'x'.repeat(9000), summary_fa: 'y'.repeat(9000) })]);
set('approved', []); set('published', []);
await send(cb('approve:big1'));
const pub4 = calls.find(c => c.method === 'sendMessage' && c.body.chat_id === '@chan');
check('پیام بلند از ۴۰۰۰ کاراکتر بیشتر نمی‌شود', pub4.body.text.length <= 4000);

await send(msg('/status'));
check('/status بدون خطا و بدون Gemini', !last('sendMessage').body.text.includes('Gemini'));

console.log('Bale mirror:');
env.BALE_BOT_TOKEN = 'bt'; env.BALE_CHANNEL_ID = '@bale_chan';
set('pending', [item('b1')]); set('approved', []); set('published', []);
await send(cb('approve:b1'));
const bale = calls.find(c => c.method === 'sendMessage' && c.body.chat_id === '@bale_chan');
check('تایید → همان خبر به بله هم می‌رود (متن ساده، بدون parse_mode)', !!bale && !('parse_mode' in bale.body) && !bale.body.text.includes('<b>'));
delete env.BALE_BOT_TOKEN; delete env.BALE_CHANNEL_ID;
set('pending', [item('b2')]); set('approved', []); set('published', []);
await send(cb('approve:b2'));
check('بدون توکن بله: هیچ درخواستی به بله نمی‌رود', !calls.some(c => c.body.chat_id === '@bale_chan'));
const realFetch = globalThis.fetch;
env.BALE_BOT_TOKEN = 'bt'; env.BALE_CHANNEL_ID = '@bale_chan';
globalThis.fetch = async (url, opts) => { if (String(url).includes('bale.ai')) throw new Error('blocked'); return realFetch(url, opts); };
set('pending', [item('b3')]); set('approved', []); set('published', []);
await send(cb('approve:b3'));
check('خطای بله انتشار تلگرام را مختل نمی‌کند و چیزی ذخیره نمی‌شود', !!calls.find(c => c.body.chat_id === '@chan')
  && get('published').length === 1 && !JSON.stringify(get('published')).includes('bale'));
globalThis.fetch = realFetch; delete env.BALE_BOT_TOKEN; delete env.BALE_CHANNEL_ID;

console.log('Auto publish:');
set('state', { paused: false, auto_publish_enabled: true });
await send(msg('/status'));
check('/status: دکمه‌های روشن/خاموش انتشار خودکار',
  last('sendMessage').body.reply_markup.inline_keyboard.flat()
    .some(b => b.callback_data === 'auto_on') &&
  last('sendMessage').body.reply_markup.inline_keyboard.flat()
    .some(b => b.callback_data === 'auto_off'));

await send(cb('auto_off'));
check('کلیک «خاموش»: state خاموش + ثبت توسط ادمین',
  get('state').auto_publish_enabled === false && get('state').auto_publish_set_by_admin === true);
check('کلیک «خاموش»: پاسخ callback',
  last('answerCallbackQuery').body.text.includes('خاموش'));

await send(cb('auto_on'));
check('کلیک «روشن»: state روشن', get('state').auto_publish_enabled === true);

for (const [cmd, expected] of [
  ['/auto off', false], ['/auto_on', true], ['/auto-off', false],
  ['/auto@MyBot on', true], ['/autoon', true], ['auto_off', false],
]) {
  await send(msg(cmd));
  check(`دستور ${cmd}`, get('state').auto_publish_enabled === expected);
}

set('state', { paused: false, auto_publish_enabled: true });
await send(msg('/auto'));
check('دستور /auto بدون آرگومان: منوی انتخاب',
  last('sendMessage').body.reply_markup.inline_keyboard.flat()
    .some(b => b.callback_data === 'auto_off'));

await send(cb('old_unknown_button'));
check('دکمهٔ ناشناس: بازخورد به کاربر (نه سکوت)',
  last('answerCallbackQuery').body.text.length > 0);

console.log('Start menu:');
set('state', { paused: false, auto_publish_enabled: true, current_stage: 'idle' });
set('pending', [item('s1')]);
set('approved', [item('s2')]);
await send(msg('/start'));
const startMsg = last('sendPhoto').body;
check('/start: بنر اختصاصی ارسال می‌شود',
  !!startMsg && startMsg.photo.includes('assets/banner.jpg'));
check('/start: عنوان و وضعیت لحظه‌ای',
  startMsg.caption.includes('ربات مدیریت اخبار AI') &&
  startMsg.caption.includes('انتشار خودکار: روشن') &&
  startMsg.caption.includes('مرحله: در انتظار اجرا'));
check('/start: شمارش فارسی صف',
  startMsg.caption.includes('در انتظار تایید: <b>۱</b>') &&
  startMsg.caption.includes('آماده انتشار: <b>۱</b>'));
check('/start: طول کپشن مجاز (زیر ۱۰۲۴)', startMsg.caption.length < 1024);
check('/start: همهٔ دکمه‌های منو',
  ['cmd:status', 'cmd:pending', 'cmd:sources', 'cmd:feeds', 'cmd:stats', 'cmd:runs', 'cmd:clear', 'auto_on', 'auto_off']
    .every(d => startMsg.reply_markup.inline_keyboard.flat().some(b => b.callback_data === d)));

// اگر تلگرام نتواند بنر را بگیرد، منو باید متنی بیاید (نه اینکه کلاً نیاید)
const fetchWithPhoto = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  if (String(url).endsWith('/sendPhoto')) {
    return { json: async () => ({ ok: false, description: 'failed to get HTTP URL content' }) };
  }
  return fetchWithPhoto(url, opts);
};
await send(msg('/start'));
check('/start: fallback متنی وقتی بنر لود نمی‌شود',
  last('sendMessage').body.text.includes('ربات مدیریت اخبار AI'));
globalThis.fetch = fetchWithPhoto;

await send(cb('cmd:status'));
check('دکمهٔ «وضعیت»: گزارش وضعیت باز می‌شود',
  last('sendMessage').body.text.includes('وضعیت ربات'));

await send(cb('cmd:clear'));
check('دکمهٔ «پاک‌کردن صف»: صفحهٔ تایید باز می‌شود',
  last('sendMessage').body.reply_markup.inline_keyboard.flat()
    .some(b => b.callback_data === 'clrp'));

console.log('Cron dispatch:');
{
  const calls2 = [];
  const rf = globalThis.fetch;
  globalThis.fetch = async (url, opts) => { calls2.push({ url: String(url), body: JSON.parse(opts.body) }); return { status: 204 }; };
  const pend = [];
  await worker.scheduled({}, { ...env, GITHUB_TOKEN: 'g', GITHUB_REPO: 'o/r' }, { waitUntil: p => pend.push(p) });
  await Promise.all(pend);
  globalThis.fetch = rf;
  check('cron Cloudflare → workflow_dispatch با trigger=cron (بدون force)',
    calls2.length === 1 && calls2[0].url.includes('/actions/workflows/pipeline.yml/dispatches')
    && calls2[0].body.inputs.trigger === 'cron');
}

console.log('Edu toggle:');
set('state', { paused: false });
await send(msg('/edu off'));
check('/edu off در state ذخیره می‌شود', get('state').edu_enabled === false);
await send(msg('/edu'));
check('/edu وضعیت خاموش را نشان می‌دهد', last('sendMessage').body.text.includes('خاموش'));
await send(msg('/edu on'));
check('/edu on دوباره روشن می‌کند', get('state').edu_enabled === true);

console.log(`\n${passed} تست Worker با موفقیت گذشت ✅`);
