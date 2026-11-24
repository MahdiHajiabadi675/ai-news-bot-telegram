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
  let body = {};
  if (typeof opts?.body === 'string') {
    try { body = JSON.parse(opts.body); } catch { body = {}; }
  }
  const method = String(url).split('/').pop();
  calls.push({ method, body });
  // پیام‌های تلگرام یک message_id برمی‌گردانند؛ /undo و /editlast به آن نیاز دارند
  // مثل تلگرام واقعی: پیام بیش از ۴۰۹۶ کاراکتر رد می‌شود (باگ قدیمی /feeds همین بود)
  if (method === 'sendMessage' && String(body.text || '').length > 4096) {
    return { json: async () => ({ ok: false, error_code: 400, description: 'Bad Request: message is too long' }) };
  }
  return { json: async () => (method === 'sendMessage' ? { ok: true, result: { message_id: 9000 + calls.length } } : { ok: true }) };
};
const env = { BOT_TOKEN: 't', CHANNEL_ID: '@chan', ADMIN_ID: String(ADMIN), KV, WEBHOOK_SECRET: 's3cret' };
const SECRET_HEADER = { 'X-Telegram-Bot-Api-Secret-Token': 's3cret' };
const set = (k, v) => store.set(k, JSON.stringify(v));
const get = (k) => (store.has(k) ? JSON.parse(store.get(k)) : null);
const last = (method) => [...calls].reverse().find(c => c.method === method);

async function send(update) {
  calls.length = 0;
  const res = await worker.fetch(new Request('https://w.example/webhook', {
    method: 'POST', headers: SECRET_HEADER, body: JSON.stringify(update),
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

console.log('Feed health — many feeds (message length):');
{
  const big = {};
  for (let i = 0; i < 55; i++) {
    big['Feed number ' + i] = { ok: i % 9 !== 0, items: 10, new: 2, filtered: 3, old: 1, err: 'HTTPError: 500',
      filtered_titles: ['A fairly long filtered headline about phones and cameras ' + i, 'Another long headline that was filtered out ' + i, 'Third sample ' + i],
      newest: new Date(Date.now() - 3600000).toISOString(), checked: '2026-10-25T10:00:00Z', fails: i % 9 === 0 ? 4 : 0 };
  }
  set('feed_health', big);
  await send(cb('cmd:feeds'));
  const sent = calls.filter(c => c.method === 'sendMessage');
  const joined = sent.map(c => c.body.text).join('\n');
  check('/feeds با ۵۵ فید: حداقل یک پیام واقعاً فرستاده شد', sent.length >= 1);
  check('/feeds: هر پیام زیر سقف تلگرام', sent.every(c => c.body.text.length <= 4096));
  check('/feeds: هیچ فیدی جا نمی‌افتد', Array.from({ length: 55 }, (_, i) => 'Feed number ' + i + (i % 9 === 0 ? '</b>' : ':')).every(n => joined.includes(n)));
  check('/feeds: نمونهٔ ردشده‌ها محدود است', (joined.match(/🚫/g) || []).length <= 10);
}

console.log('Dashboard:');
{
  const nowIso = new Date().toISOString();
  set('run_log', [{ t: nowIso, collected: true, fetched: 10, analyzed: 8, auto: 3, pending: 2, kv_put: 14, published: 3 }]);
  set('published', [
    { published: true, category: 'model_release', published_at: nowIso },
    { published: false, reject_reason: 'low_score', rejected_at: nowIso },
  ]);
  set('heartbeat', { at: nowIso, modules: { views: { ok: false, fails: 3, err: 'KeyError: x' }, edu: { ok: true, fails: 0 } } });
  set('edu_state', { queue: [1], pub_count: {}, usage: {} });
  await send(cb('cmd:dashboard'));
  const dtext = calls.filter(c => c.method === 'sendMessage').map(c => c.body.text).join('\n');
  check('/dashboard: شمارش‌ها', dtext.includes('منتشرشده: 1') && dtext.includes('ردشده: 1'));
  check('/dashboard: نرخ خودکار', dtext.includes('۶۰٪') || dtext.includes('60٪'));
  check('/dashboard: ماژول خراب', dtext.includes('views') && dtext.includes('پیاپی: 3'));
  check('/dashboard: نوشتن KV', dtext.includes('14'));
  set('heartbeat', {}); set('run_log', []); set('published', []);
  await send(msg('/dashboard'));
  check('/dashboard خالی خطا نمی‌دهد', calls.some(c => c.method === 'sendMessage'));
}

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
{
  // قاعدهٔ پروژه: هر دستور منو هم دکمه دارد و هم در لیست متن /start آمده است
  const btns = startMsg.reply_markup.inline_keyboard.flat().map(b => b.callback_data);
  for (const c of ['status', 'pending', 'sources', 'feeds', 'runs', 'dashboard', 'ask', 'quizboard', 'stats', 'top', 'weekly', 'tips', 'backup', 'clear']) {
    check(`/start: دکمهٔ ${c}`, btns.includes('cmd:' + c));
  }
  check('/start: /dashboard در لیست متن', startMsg.caption.includes('/dashboard'));
}
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
set('edu_state', { queue: [{ post: { title: 'x' } }, { post: { title: 'y' } }], recent: [] });
await send(msg('/edu'));
check('/edu تعداد صف انتشار را نشان می‌دهد', last('sendMessage').body.text.includes('در صف انتشار') && last('sendMessage').body.text.includes('2'));
const _d0 = new Date(Date.now() + 210 * 60000).toISOString().slice(0, 10);
set('edu_state', { queue: [], recent: [], usage: { [_d0]: { attempts: 3, ok: 1, fail: 2, calls: 9, rejected: 2, via: { 'gemini/g-1': 1 } } } });
await send(msg('/edu'));
check('/edu آمار مصرف امروز را نشان می‌دهد', last('sendMessage').body.text.includes('ساخت 1 از 3 تلاش') && last('sendMessage').body.text.includes('توکن هدررفته) 2') && last('sendMessage').body.text.includes('gemini/g-1'));
await send(msg('/edu on'));
check('/edu on دوباره روشن می‌کند', get('state').edu_enabled === true);

// ───────────── امنیت وبهوک ─────────────
console.log('Webhook auth:');
{
  const body = JSON.stringify(cb('clra'));
  const post = (headers, e = env) => worker.fetch(
    new Request('https://w.example/webhook', { method: 'POST', headers, body }), e);

  check('بدون secret_token → 403', (await post({})).status === 403);
  check('secret_token اشتباه → 403', (await post({ 'X-Telegram-Bot-Api-Secret-Token': 'nope' })).status === 403);

  const envNoSecret = { ...env, WEBHOOK_SECRET: '' };
  check('بدون secret و IP غیرتلگرام → 403',
    (await post({ 'CF-Connecting-IP': '8.8.8.8' }, envNoSecret)).status === 403);
  check('بدون secret ولی IP تلگرام → پذیرفته می‌شود',
    (await post({ 'CF-Connecting-IP': '149.154.167.99' }, envNoSecret)).status === 200);
  check('رنج دوم IP تلگرام هم پذیرفته می‌شود',
    (await post({ 'CF-Connecting-IP': '91.108.5.5' }, envNoSecret)).status === 200);
}

// ───────────── ضدتکرار ─────────────
console.log('Replay guard:');
{
  set('pending', [item('rp1')]); set('approved', []); set('published', []);
  const upd = { update_id: 9001, ...cb('approve:rp1') };
  await send(upd);
  const firstPosts = calls.filter(c => c.method === 'sendMessage' && c.body.chat_id === '@chan').length;
  await send(upd); // همان update دوباره (تحویل تکراری تلگرام)
  const secondPosts = calls.filter(c => c.method === 'sendMessage' && c.body.chat_id === '@chan').length;
  check('update تکراری دوباره در کانال پست نمی‌شود', firstPosts === 1 && secondPosts === 0);
}

// ───────────── قالب ایمن (HTML/لینک) ─────────────
console.log('Safe formatting:');
{
  const tagsBalanced = (t) =>
    (t.match(/<b>/g) || []).length === (t.match(/<\/b>/g) || []).length &&
    (t.match(/<i>/g) || []).length === (t.match(/<\/i>/g) || []).length &&
    (t.match(/<blockquote/g) || []).length === (t.match(/<\/blockquote>/g) || []).length;

  set('pending', [item('long1', { summary_fa: 'ی'.repeat(9000), summary: 'x'.repeat(9000) })]);
  set('approved', []); set('published', []);
  await send(cb('approve:long1'));
  const longText = calls.find(c => c.body.chat_id === '@chan').body.text;
  check(`پیام بلند: سقف ۴۰۰۰ و تگ سالم (طول=${longText.length}, سالم=${tagsBalanced(longText)})`,
    longText.length <= 4000 && tagsBalanced(longText));

  set('pending', [item('badlink', { link: 'javascript:alert(1)' })]);
  set('approved', []); set('published', []);
  await send(cb('approve:badlink'));
  const badText = calls.find(c => c.body.chat_id === '@chan').body.text;
  check('لینک نامعتبر → بدون href و بدون javascript',
    !badText.includes('javascript:') && !badText.includes('<a href=""'));

  // امضای کانال (اختیاری)
  set('state', { paused: false, auto_publish_enabled: true, daily_post_count: 0,
    daily_post_date: new Date().toISOString().slice(0, 10) });
  set('pending', [item('sig1')]); set('approved', []); set('published', []);
  calls.length = 0;
  await worker.fetch(new Request('https://w.example/webhook', {
    method: 'POST', headers: SECRET_HEADER, body: JSON.stringify(cb('approve:sig1')),
  }), { ...env, CHANNEL_SIGNATURE: '@my_channel' });
  const sigText = calls.find(c => c.body.chat_id === '@chan').body.text;
  check('امضای کانال در انتهای پست می‌آید', sigText.trimEnd().endsWith('@my_channel'));
}

// ───────────── undo / ویرایش پست منتشرشده ─────────────
console.log('Undo & edit published:');
{
  // شمارندهٔ راند را صفر کن تا سقف ۱۵ پست، این تست را خراب نکند
  set('state', { paused: false, auto_publish_enabled: true, daily_post_count: 0,
    daily_post_date: new Date().toISOString().slice(0, 10) });
  set('pending', [item('u1')]); set('approved', []); set('published', []);
  await send(cb('approve:u1'));
  const pub = get('published').find(p => p.published);
  check('انتشار: message_id ذخیره می‌شود', !!pub && typeof pub.message_id === 'number');

  await send(msg('/undo'));
  const kbd = last('sendMessage').body.reply_markup.inline_keyboard.flat().map(b => b.callback_data);
  check('undo: اول تایید می‌خواهد و چیزی را حذف نمی‌کند',
    kbd.includes('undo_yes') && kbd.includes('undo_no') && !calls.some(c => c.method === 'deleteMessage'));

  await send(cb('undo_yes'));
  check('undo: از کانال حذف و در لاگ علامت می‌خورد',
    calls.some(c => c.method === 'deleteMessage' && c.body.message_id === pub.message_id)
    && get('published').find(p => p.message_id === pub.message_id).deleted === true);

  set('pending', [item('u2')]); set('approved', []); set('published', []);
  set('state', { paused: false, auto_publish_enabled: true, daily_post_count: 0,
    daily_post_date: new Date().toISOString().slice(0, 10) });
  await send(cb('approve:u2'));
  await send(msg('/editlast'));
  await send(msg('خلاصهٔ کاملاً تازه برای کانال'));
  check('editlast: editMessageText با متن جدید صدا زده می‌شود',
    calls.some(c => c.method === 'editMessageText' && String(c.body.text).includes('خلاصهٔ کاملاً تازه')));
  check('editlast: لاگ به‌روز و حالت ویرایش پاک می‌شود',
    get('published').some(p => String(p.summary_fa).startsWith('خلاصهٔ کاملاً تازه'))
    && !get('state')._editing_pub);
}

// ───────────── /health ─────────────
console.log('Health endpoint:');
{
  set('heartbeat', { at: '2026-11-14T09:00:00Z', published: 3 });
  const res = await worker.fetch(new Request('https://w.example/health'), env);
  const body = await res.json();
  check('/health: 200 با KV سالم', res.status === 200 && body.ok === true && body.kv === true);
  check('/health: شمارنده‌ها و آخرین اجرا',
    typeof body.queue.pending === 'number' && typeof body.published === 'number'
    && body.lastHeartbeat === '2026-11-14T09:00:00Z');
  check('/health: هیچ عنوان خبری لو نمی‌رود', !JSON.stringify(body).includes('عنوان'));

  const resSlash = await worker.fetch(new Request('https://w.example/health/'), env);
  check('/health/ با اسلش آخر هم کار می‌کند', resSlash.status === 200 && (await resSlash.json()).ok === true);

  await send(msg('/health'));
  const htxt = last('sendMessage').body.text;
  check('/health داخل ربات: متن سلامت با KV، صف و وضعیت آموزشی',
    htxt.includes('سلامت ربات') && htxt.includes('KV') && htxt.includes('آموزشی امروز'));
}

// ───────────── انتشار زمان‌بندی‌شده ─────────────
console.log('Scheduled publishing:');
{
  set('state', { paused: false, auto_publish_enabled: true, daily_post_count: 0,
    daily_post_date: new Date().toISOString().slice(0, 10) });
  set('pending', [item('sc1')]); set('approved', []); set('published', []);
  await send(cb('sched:2:sc1'));
  const appr = get('approved');
  check('sched: خبر با زمان آینده در صف «آماده انتشار» می‌ماند',
    appr.length === 1 && new Date(appr[0].schedule_at) > new Date());
  check('sched: همان لحظه منتشر نمی‌شود', !calls.some(c => c.body.chat_id === '@chan'));

  set('pending', [item('sc3', { schedule_at: new Date(Date.now() + 3600000).toISOString() })]);
  set('approved', []); set('published', []);
  await send(cb('approve:sc3'));
  check('publishItem: خبر زمان‌بندی‌شده فوری منتشر نمی‌شود',
    !get('published').some(p => p.published));
}

// ───────────── خروجی عمومی RSS و API ─────────────
console.log('Public feed & API:');
{
  set('published', [
    { id: 'p1', title_fa: 'خبر تازه', source: 'Src', link: 'https://example.com/a',
      category: 'model_release', published: true, published_at: new Date().toISOString(), summary_fa: 'خلاصه' },
    { id: 'p2', title_fa: 'خبر حذف‌شده', published: true, deleted: true, published_at: new Date().toISOString() },
    { id: 'p3', title_fa: 'خبر ردشده', published: false },
  ]);
  const rss = await worker.fetch(new Request('https://w.example/rss.xml'), env);
  const xml = await rss.text();
  check('rss: خروجی معتبر و شامل خبر منتشرشده', rss.status === 200 && xml.includes('<rss') && xml.includes('خبر تازه'));
  check('rss: خبر حذف‌شده و ردشده نمی‌آید', !xml.includes('خبر حذف‌شده') && !xml.includes('خبر ردشده'));

  const api = await worker.fetch(new Request('https://w.example/api/news?limit=10'), env);
  const body = await api.json();
  check('api/news: JSON با شمارش و فیلدهای لازم',
    api.status === 200 && body.count === 1 && body.items[0].link === 'https://example.com/a'
    && body.items[0].category === 'model_release');
  check('api/news: نسخهٔ ساده فیلد اضافه (id/summary) ندارد', body.items[0].id === undefined && body.items[0].summary === undefined);
  check('api/news: CORS باز', api.headers.get('Access-Control-Allow-Origin') === '*');

  set('published', [
    { id: 'abc123', title_fa: 'خبر کامل', summary_fa: 'خلاصهٔ خبر', source: 'S', link: 'https://example.com/a', category: 'research',
      published: true, published_at: new Date().toISOString(), message_id: 42, hashtags: ['AI', 'LLM'] },
  ]);
  const apiFull = await worker.fetch(new Request('https://w.example/api/news?full=1&limit=300'), env);
  const fb = await apiFull.json();
  check('api/news?full=1: فیلدهای وب‌سایت', fb.items[0].id === 'abc123' && fb.items[0].summary === 'خلاصهٔ خبر'
    && fb.items[0].message_id === 42 && fb.items[0].tags.length === 2 && fb.channel === 'chan');
  const many = Array.from({ length: 350 }, (_, i) => ({ id: 'n' + i, title_fa: 't' + i, published: true, published_at: new Date().toISOString() }));
  set('published', many);
  const capped = await (await worker.fetch(new Request('https://w.example/api/news?full=1&limit=999'), env)).json();
  const cappedPlain = await (await worker.fetch(new Request('https://w.example/api/news?limit=999'), env)).json();
  check('api/news: سقف ۳۰۰ برای full و ۱۰۰ برای ساده', capped.count === 300 && cappedPlain.count === 100);
}

// ───────────── آینهٔ Bluesky ─────────────
console.log('Bluesky mirror:');
{
  const rf = globalThis.fetch;
  const seen = [];
  globalThis.fetch = async (url, opts) => {
    const body = opts?.body ? JSON.parse(opts.body) : {};
    seen.push({ url: String(url), body, headers: opts?.headers || {} });
    if (String(url).includes('createSession')) {
      return { json: async () => ({ accessJwt: 'jwt', did: 'did:plc:x' }), status: 200 };
    }
    return { json: async () => ({ ok: true }), status: 200 };
  };
  set('state', { paused: false, auto_publish_enabled: true, daily_post_count: 0,
    daily_post_date: new Date().toISOString().slice(0, 10) });
  set('pending', [item('bs1')]); set('approved', []); set('published', []);
  const envB = { ...env, BLUESKY_HANDLE: 'me.bsky.social', BLUESKY_APP_PASSWORD: 'pw' };
  await worker.fetch(new Request('https://w.example/webhook', {
    method: 'POST', headers: SECRET_HEADER, body: JSON.stringify(cb('approve:bs1')),
  }), envB);
  const rec = seen.find(s => s.url.includes('createRecord'));
  check('Bluesky: بعد از انتشار createRecord با did و متن خبر صدا زده می‌شود',
    !!rec && rec.body.repo === 'did:plc:x' && rec.body.record.text.includes('عنوان'));
  check('Bluesky: توکن در هدر Authorization می‌رود', !!rec && rec.headers.Authorization === 'Bearer jwt');

  // لینک با خلاصهٔ بلند باید کامل بماند (سقف ۲۸۰)
  set('state', { paused: false, auto_publish_enabled: true, daily_post_count: 0,
    daily_post_date: new Date().toISOString().slice(0, 10) });
  set('pending', [item('bs2', { summary_fa: 'ی'.repeat(600) })]);
  set('approved', []); set('published', []);
  await worker.fetch(new Request('https://w.example/webhook', {
    method: 'POST', headers: SECRET_HEADER, body: JSON.stringify(cb('approve:bs2')),
  }), envB);
  const rec2 = seen.filter(s => s.url.includes('createRecord')).pop();
  check('Bluesky: لینک با خلاصهٔ بلند کامل می‌ماند',
    !!rec2 && rec2.body.record.text.endsWith('http://x') && rec2.body.record.text.length <= 280);
  globalThis.fetch = rf;
}

// ───────────── قابلیت‌های تعامل ─────────────
console.log('Engagement features:');
{
  const tkey = new Date(Date.now() + 210 * 60000).toISOString().slice(0, 10);
  set('published', [{ id: 'p1', title_fa: 'خبر', published: true, source: 'منبع' }]);
  set('edu_state', {
    queue: [{ post: { title: 'a' } }, { post: { title: 'b' } }, { post: { title: 'c' } }],
    pub_count: { [tkey]: 4 },
    quiz_count: { [tkey]: 1 },
  });
  await send(msg('/edu'));
  const eduText = last('sendMessage').body.text;
  check('/edu: پست‌های آمادهٔ انتشار', eduText.includes('در صف انتشار: 3 پست'));
  check('/edu: کوییز باقی‌ماندهٔ امشب', eduText.includes('1 کوییز باقی‌ماندهٔ امشب'));
  check('/edu: منتشرشدهٔ امروز', eduText.includes('4/4 پست') && eduText.includes('1/2 کوییز'));

  // 📌 تایید و سنجاق
  set('state', { paused: false, auto_publish_enabled: true, daily_post_count: 0,
    daily_post_date: new Date().toISOString().slice(0, 10) });
  set('pending', [item('pin1')]); set('approved', []); set('published', []);
  await send(cb('pin:pin1'));
  const pinnedPub = get('published').find(p => p.published);
  const pinCall = calls.find(c => c.method === 'pinChatMessage');
  check('pin: فوری منتشر و در کانال سنجاق می‌شود',
    !!pinnedPub && !!pinCall && pinCall.body.message_id === pinnedPub.message_id);

  // 👋 پست خوش‌آمد
  set('published', [
    { id: 'w1', title_fa: 'خبر یک', published: true, link: 'http://a' },
    { id: 'w2', title_fa: 'خبر دو', published: true, link: 'http://b' },
  ]);
  await send(msg('/welcome'));
  const welcomeMsg = calls.filter(c => c.method === 'sendMessage' && c.body.chat_id === '@chan').pop();
  const welcomePin = calls.find(c => c.method === 'pinChatMessage');
  check('/welcome: پست خوش‌آمد در کانال با لینک خبرها', !!welcomeMsg
    && welcomeMsg.body.text.includes('خوش آمدی') && welcomeMsg.body.text.includes('خبر یک'));
  check('/welcome: پست خوش‌آمد سنجاق می‌شود', !!welcomePin);

  // آینهٔ بله برای خوش‌آمد (و هر متن ساده‌ای که قالب خبر ندارد)
  const rfW = globalThis.fetch;
  const seenBale = [];
  globalThis.fetch = async (url, opts) => {
    if (String(url).includes('tapi.bale.ai')) {
      seenBale.push(JSON.parse(opts.body));
      return { json: async () => ({ ok: true }), status: 200 };
    }
    return rfW(url, opts);
  };
  await worker.fetch(new Request('https://w.example/webhook', {
    method: 'POST', headers: SECRET_HEADER, body: JSON.stringify(msg('/welcome')),
  }), { ...env, BALE_BOT_TOKEN: 'bt', BALE_CHANNEL_ID: '@bale' });
  globalThis.fetch = rfW;
  check('/welcome: نسخهٔ بله هم فرستاده می‌شود',
    seenBale.length === 1 && seenBale[0].text.includes('خوش آمدی'));

  // 🗂 آرشیو موضوعی در منوی /start
  await send(msg('/start'));
  const startCall = calls.find(c => c.method === 'sendPhoto') || last('sendMessage');
  const startKb = startCall.body.reply_markup;
  const flatStart = startKb && startKb.inline_keyboard ? startKb.inline_keyboard.flat() : [];
  check('/start: دکمهٔ آرشیو موضوعی', flatStart.some(b => b.url && b.url.includes('t.me/s/chan')));

  // 🎯 موضوعات مورد علاقه
  set('state', { paused: false, auto_publish_enabled: true, daily_post_count: 0, topics: [] });
  await send(msg('/topics add ایمنی'));
  check('/topics add: موضوع ذخیره می‌شود', (get('state').topics || []).includes('ایمنی'));
  await send(msg('/topics'));
  check('/topics: فهرست موضوعات را نشان می‌دهد',
    last('sendMessage').body.text.includes('ایمنی') && last('sendMessage').body.text.includes('topics add'));
  await send(msg('/topics del ایمنی'));
  check('/topics del: موضوع حذف می‌شود', (get('state').topics || []).length === 0);
  await send(msg('/topics add ایجنت'));
  await send(msg('/topics clear'));
  check('/topics clear: همه پاک می‌شوند', (get('state').topics || []).length === 0);

  check('/welcome: متن حرفه‌ای با منبع‌ها و دعوت به فوروارد',
    welcomeMsg.body.text.includes('۳۷ منبع') && welcomeMsg.body.text.includes('فوروارد'));

  // 👁 پربازدیدترین‌ها (بازخورد بازدید)
  set('published', [
    { id: 'v1', title_fa: 'خبر پربازدید', published: true, views: 4200, source: 'منبع' },
    { id: 'v2', title_fa: 'خبر کم‌بازدید', published: true, views: 120, source: 'منبع' },
    { id: 'v3', title_fa: 'بدون بازدید', published: true, source: 'منبع' },
  ]);
  await send(msg('/top'));
  const topText = last('sendMessage').body.text;
  check('/top: پربازدیدترین اول می‌آید',
    topText.includes('خبر پربازدید') && topText.indexOf('خبر پربازدید') < topText.indexOf('خبر کم‌بازدید'));
  check('/top: بازدید خلاصه‌شده (4.2K)', topText.includes('4.2K'));

  // ⚠️ کلید مرده: heartbeat کهنه باید به ادمین هشدار بدهد
  set('heartbeat', { at: new Date(Date.now() - 5 * 3600 * 1000).toISOString() });
  store.delete('alert_state');
  calls.length = 0;
  const pends = [];
  await worker.fetch(new Request('https://w.example/health'), env, { waitUntil: p => pends.push(p) });
  await Promise.all(pends);
  const staleAlert = calls.find(c => c.method === 'sendMessage' && String(c.body.text).includes('از کار افتاده'));
  check('هشدار: پایپ‌لاین کهنه به ادمین اطلاع می‌دهد', !!staleAlert);

  calls.length = 0;
  const pends2 = [];
  await worker.fetch(new Request('https://w.example/health'), env, { waitUntil: p => pends2.push(p) });
  await Promise.all(pends2);
  const secondAlert = calls.find(c => c.method === 'sendMessage' && String(c.body.text).includes('از کار افتاده'));
  check('هشدار: حداکثر هر ۳ ساعت یک‌بار تکرار می‌شود', !secondAlert);

  // ───────── حلقهٔ تعامل v2 ─────────
  console.log('Engagement loop v2:');
  const publicMsg = (text, chatId = 555) => ({
    message: {
      text, chat: { id: chatId, type: 'private' },
      from: { id: chatId, first_name: 'کاربر', username: 'reader' },
    },
  });

  store.delete('reactions');
  await send({ message_reaction_count: {
    message_id: 777,
    reactions: [{ type: { emoji: '🔥' }, total_count: 5 }, { type: { emoji: '👍' }, total_count: 2 }],
  } });
  const rx = get('reactions');
  check('واکنش: آپدیت کانال در KV ذخیره می‌شود', rx && rx['777'] && rx['777'].total === 7);
  check('واکنش: تفکیک ایموجی ذخیره می‌شود', rx['777'].by['🔥'] === 5);
  set('published', [{ id: 'rx1', message_id: 777, title_fa: 'خبر واکنشی', published: true }]);
  await send(msg('/top'));
  const rxTop = last('sendMessage').body.text;
  check('/top: خبر بدون بازدید ولی با واکنش هم می‌آید', rxTop.includes('خبر واکنشی') && rxTop.includes('🔥'));

  set('subscribers', []);
  await send(publicMsg('/subscribe'));
  check('/subscribe: کاربر خصوصی مشترک می‌شود',
    last('sendMessage').body.chat_id === 555 && last('sendMessage').body.text.includes('اشتراک فعال')
    && (get('subscribers') || []).length === 1);
  await send(publicMsg('/subscribe'));
  check('/subscribe: اشتراک تکراری ثبت نمی‌شود',
    last('sendMessage').body.text.includes('از قبل مشترک') && (get('subscribers') || []).length === 1);

  store.delete('tips');
  store.delete('tip_rate');
  await send(publicMsg('/tip OpenAI مدل تازه‌ای معرفی کرد'));
  const tipRows = get('tips') || [];
  check('/tip: سوژهٔ مخاطب ذخیره می‌شود', tipRows.length === 1 && tipRows[0].status === 'new');
  const tipNotice = calls.find(c => c.method === 'sendMessage' && c.body.chat_id === ADMIN
    && c.body.reply_markup);
  check('/tip: ادمین دکمهٔ تایید/رد می‌گیرد',
    !!tipNotice && JSON.stringify(tipNotice.body.reply_markup).includes('tipok:'));

  await send(publicMsg('/tip یک سوژهٔ دوم در همان ساعت'));
  check('/tip: محدودیت نرخ ساعتی',
    last('sendMessage').body.text.includes('سهمیه') && (get('tips') || []).length === 1);

  calls.length = 0;
  await send(cb(`tipok:${tipRows[0].id}`));
  const tipPub = calls.find(c => c.method === 'sendMessage' && c.body.chat_id === '@chan');
  check('/tip: سوژهٔ تاییدشده در کانال منتشر می‌شود',
    !!tipPub && tipPub.body.text.includes('از مخاطبان') && (get('tips') || [])[0].status === 'approved');

  await send(publicMsg('/unsubscribe'));
  check('/unsubscribe: اشتراک قطع می‌شود',
    last('sendMessage').body.text.includes('قطع شد') && (get('subscribers') || []).length === 0);

  set('weekly_state', {
    last_date: '2026-01-03',
    poll: { closed: true, total_votes: 9 },
    result: { category: 'tool_release', label: 'ابزار و محصول', votes: 6, total: 9,
      expires_at: new Date(Date.now() + 86400000).toISOString() },
  });
  await send(msg('/weekly'));
  const weeklyTxt = last('sendMessage').body.text;
  check('/weekly: نتیجهٔ نظرسنجی را نشان می‌دهد',
    weeklyTxt.includes('انتخاب مخاطبان') && weeklyTxt.includes('ابزار و محصول'));

  set('tips', [{ id: 'tip-1', chat_id: 555, user: 'کاربر', text: 'سوژهٔ تست', status: 'new' }]);
  await send(msg('/tips'));
  const tipsCall = [...calls].reverse().find(c => c.method === 'sendMessage' && c.body.reply_markup);
  check('/tips: فهرست سوژه‌ها با دکمهٔ بررسی',
    !!tipsCall && JSON.stringify(tipsCall.body.reply_markup).includes('tipok:tip-1'));

  calls.length = 0;
  await send(msg('/backup'));
  check('/backup: فایل پشتیبان به ادمین می‌رود',
    !!calls.find(c => c.method === 'sendDocument')
    && last('sendMessage').body.text.includes('پشتیبان ارسال شد'));

  set('pending', [item('share1')]);
  set('approved', []);
  set('published', []);
  set('state', { paused: false, auto_publish_enabled: true, daily_post_count: 0 });
  await send(cb('approve:share1'));
  const shareCall = calls.find(c => c.method === 'editMessageReplyMarkup'
    && JSON.stringify(c.body.reply_markup || {}).includes('t.me/share'));
  check('اشتراک‌گذاری: دکمهٔ «فرستادن به دوست» زیر پست می‌آید', !!shareCall);
}


// ───────── /ask: جست‌وجوی آرشیو ─────────
console.log('Archive search (/ask):');
{
  const dm = (text, chatId = 901) => ({ message: { text, chat: { id: chatId, type: 'private' }, from: { id: chatId, first_name: 'ک' } } });
  const now = new Date().toISOString();
  set('published', [
    { published: true, title_fa: 'OpenAI مدل جدید معرفی کرد', summary_fa: 'خلاصه', hashtags: ['OpenAI'], category: 'model_release', link: 'https://x.test/1', published_at: now, message_id: 11 },
    { published: true, title_fa: 'انتشار مدل‌های متن‌باز Llama', summary_fa: 'متا مدل تازه داد', hashtags: ['Llama'], category: 'model_release', link: 'https://x.test/2', published_at: now },
    { published: true, title_fa: 'کیفیت تصویر', summary_fa: 'ي و ك عربی در متن: كتاب', hashtags: [], category: 'tool_release', link: 'https://x.test/3', published_at: now },
    { published: false, title_fa: 'OpenAI ردشده', link: 'https://x.test/4' },
    { published: true, deleted: true, title_fa: 'OpenAI حذف‌شده', link: 'https://x.test/5' },
  ]);
  store.delete('ask_rate');
  await send(dm('/ask openai'));
  let r = calls.filter(c => c.method === 'sendMessage').map(c => c.body.text).join('\n');
  check('/ask: نتیجهٔ مرتبط با لینک پست کانال', r.includes('OpenAI مدل جدید') && r.includes('https://t.me/chan/11'));
  check('/ask: ردشده و حذف‌شده نمی‌آید', !r.includes('ردشده') && !r.includes('حذف‌شده'));
  await send(dm('/ask مدل متن باز'));
  r = calls.filter(c => c.method === 'sendMessage').map(c => c.body.text).join('\n');
  check('/ask: نیم‌فاصله و پسوند (متن‌باز / مدل‌های)', r.includes('Llama') && r.includes('https://x.test/2'));
  await send(dm('/ask کتاب'));
  r = calls.filter(c => c.method === 'sendMessage').map(c => c.body.text).join('\n');
  check('/ask: یکسان‌سازی ی/ک عربی', r.includes('کیفیت تصویر'));
  await send(dm('/ask zzzzqq'));
  check('/ask: بدون نتیجه پیام راهنما', last('sendMessage').body.text.includes('پیدا نشد'));
  await send(dm('/ask'));
  check('/ask بدون کلمه: راهنمای استفاده', last('sendMessage').body.text.includes('/ask OpenAI'));
  check('/ask: لینک تزریق‌شده HTML نمی‌شود', (() => { set('published', [{ published: true, title_fa: 'x <b>OpenAI</b> "q"', link: 'javascript:alert(1)', published_at: now }]); return true; })());
  await send(dm('/ask openai', 902));
  r = last('sendMessage').body.text;
  check('/ask: عنوان escape می‌شود و لینک خطرناک حذف', r.includes('&lt;b&gt;') && !r.includes('javascript:'));

  // سهمیه
  set('published', []);
  store.delete('ask_rate');
  for (let i = 0; i < 10; i++) await send(dm('/ask gemini', 903));
  await send(dm('/ask gemini', 903));
  check('/ask: سقف ساعتی برای هر کاربر', last('sendMessage').body.text.includes('حداکثر 10'));
  await send(dm('/ask gemini', 904));
  check('/ask: کاربر دیگر محدود نیست', !last('sendMessage').body.text.includes('حداکثر'));
  const rates = get('ask_rate');
  check('/ask: شمارندهٔ روزانهٔ کل', rates._day && rates._day.n >= 11);
  const envBak = env.ASK_MAX_PER_DAY; env.ASK_MAX_PER_DAY = '10';
  await send(dm('/ask gemini', 905));
  check('/ask: سقف روزانهٔ کل', last('sendMessage').body.text.includes('امروز سهمیهٔ جست‌وجو پر شده'));
  if (envBak === undefined) delete env.ASK_MAX_PER_DAY; else env.ASK_MAX_PER_DAY = envBak;

  // ادمین: بدون سهمیه و بدون نوشتن KV
  set('published', [{ published: true, title_fa: 'Gemini تازه', link: 'https://x.test/9', published_at: now }]);
  store.delete('ask_rate');
  await send(msg('/ask gemini'));
  check('/ask ادمین: نتیجه می‌دهد و سهمیه نمی‌نویسد', last('sendMessage').body.text.includes('Gemini تازه') && !store.has('ask_rate'));
  await send(cb('cmd:ask'));
  check('دکمهٔ منوی جست‌وجو راهنما می‌دهد', last('sendMessage').body.text.includes('/ask OpenAI'));
  await send(dm('/help', 906));
  check('راهنمای عمومی: /ask معرفی شده', last('sendMessage').body.text.includes('/ask'));
}


// ───────── کوییز امتیازی خصوصی ─────────
console.log('Scored quiz (/quiz):');
{
  const dm = (text, chatId = 1001, name = 'سارا') => ({ message: { text, chat: { id: chatId, type: 'private' }, from: { id: chatId, first_name: name } } });
  const qbank = [
    { id: 'q-alpha', q: 'سؤال اول؟', o: ['غلط۱', 'درست۱', 'غلط۲', 'غلط۳'], c: 1, e: 'توضیح اول' },
    { id: 'q-beta', q: 'سؤال دوم؟', o: ['غلط۱', 'درست۲', 'غلط۲', 'غلط۳'], c: 1, e: 'توضیح دوم' },
    { id: 'q-gamma', q: 'سؤال سوم؟', o: ['غلط۱', 'درست۳', 'غلط۲', 'غلط۳'], c: 1, e: 'توضیح سوم' },
  ];
  const answerCb = (data, chatId, mid = 50) => ({ callback_query: { id: 'cbq', data, from: { id: chatId, first_name: 'سارا' }, message: { chat: { id: chatId }, message_id: mid } } });
  const served = () => {
    const m = [...calls].reverse().find(c => c.method === 'sendMessage' && c.body.reply_markup);
    const btns = m.body.reply_markup.inline_keyboard[0];
    const id = btns[0].callback_data.split(':')[1];
    const q = qbank.find(x => x.id === id);
    const right = btns.find(b => Number(b.callback_data.split(':')[2]) === q.c).callback_data;
    const wrong = btns.find(b => Number(b.callback_data.split(':')[2]) !== q.c).callback_data;
    return { m, btns, id, q, right, wrong };
  };

  store.delete('quiz_scores');
  await send(dm('/quiz'));
  check('/quiz بدون بانک: پیام آماده‌نبودن', last('sendMessage').body.text.includes('هنوز آماده نیست'));
  set('quiz_bank', qbank);
  await send(dm('/quiz'));
  let sv = served();
  check('/quiz: ۴ دکمه با برچسب الف تا د', sv.btns.length === 4 && sv.btns.map(b => b.text).join('') === 'الفبجد');
  check('/quiz: callback_data کوتاه‌تر از ۶۴ بایت', sv.btns.every(b => new TextEncoder().encode(b.callback_data).length <= 64));
  check('/quiz: پاسخ درست داخل متن پیام لو نمی‌رود', !sv.m.body.text.includes('توضیح'));
  check('/quiz: سؤال برای کاربر ثبت می‌شود', get('quiz_scores').users['1001'].seen.includes(sv.id));

  await send(answerCb(sv.right, 1001));
  let edit = last('editMessageText');
  let u = get('quiz_scores').users['1001'];
  check('پاسخ درست: ۱۰ امتیاز و توضیح', u.t === 10 && u.ok === 1 && u.tot === 1 && edit.body.text.includes('+10') && edit.body.text.includes('توضیح'));
  check('پاسخ درست: دکمهٔ سؤال بعدی', JSON.stringify(edit.body.reply_markup).includes('qz:next'));
  check('پاسخ درست: امتیاز هفتگی ثبت می‌شود', u.w && u.w.pts === 10);
  await send(answerCb(sv.right, 1001));
  check('پاسخ دوباره به همان سؤال امتیاز نمی‌دهد', get('quiz_scores').users['1001'].t === 10 && String(last('answerCallbackQuery').body.text).includes('قبلاً'));
  await send(answerCb('qz:q-zzz:1', 1001));
  check('سؤال ناموجود رد می‌شود', String(last('answerCallbackQuery').body.text).includes('دیگر در دسترس'));
  const other = qbank.find(x => x.id !== sv.id).id;
  await send(answerCb(`qz:${other}:1`, 1001));
  check('سؤالی که برای او فرستاده نشده امتیاز ندارد', get('quiz_scores').users['1001'].t === 10 && String(last('answerCallbackQuery').body.text).includes('مال تو نبود'));

  await send(answerCb('qz:next', 1001));
  sv = served();
  await send(answerCb(sv.wrong, 1001));
  u = get('quiz_scores').users['1001'];
  check('پاسخ غلط: امتیاز نمی‌دهد ولی شمرده می‌شود', u.t === 10 && u.tot === 2 && last('editMessageText').body.text.includes('این نبود'));

  await send(dm('/me'));
  const me = last('sendMessage').body.text;
  check('/me: امتیاز، رتبه و دقت', me.includes('<b>10</b>') && me.includes('رتبهٔ 1') && me.includes('1/2') && me.includes('50'));
  await send(dm('/leaderboard', 1002, 'علی'));
  check('/leaderboard: نام و امتیاز', last('sendMessage').body.text.includes('سارا') && last('sendMessage').body.text.includes('<b>10</b>'));
  await send(dm('/hide'));
  await send(dm('/leaderboard', 1002, 'علی'));
  check('/hide: نام ناشناس می‌شود', !last('sendMessage').body.text.includes('سارا') && last('sendMessage').body.text.includes('ناشناس'));
  await send(dm('/hide'));

  // سقف روزانهٔ هر کاربر
  store.delete('quiz_scores');
  env.QUIZ_MAX_PER_DAY = '2';
  await send(dm('/quiz', 1003)); await send(dm('/quiz', 1003));
  await send(dm('/quiz', 1003));
  check('سقف روزانهٔ هر کاربر', last('sendMessage').body.text.includes('امروز 2 سؤال'));
  delete env.QUIZ_MAX_PER_DAY;
  // سقف روزانهٔ کل (حفظ سقف نوشتن KV)
  env.QUIZ_MAX_SENDS_PER_DAY = '10';
  store.delete('quiz_scores');
  for (let i = 0; i < 10; i++) await send(dm('/quiz', 2000 + i));
  await send(dm('/quiz', 2999));
  check('سقف روزانهٔ کل ارسال‌ها', last('sendMessage').body.text.includes('ظرفیت کوییز امروز پر شده'));
  delete env.QUIZ_MAX_SENDS_PER_DAY;

  // ترتیب گزینه‌ها تصادفی است (جلوگیری از «همیشه ب»)
  store.delete('quiz_scores');
  const positions = new Set();
  for (let i = 0; i < 25; i++) {
    await send(dm('/quiz', 3000 + i));
    const x = served();
    positions.add(x.btns.findIndex(b => Number(b.callback_data.split(':')[2]) === x.q.c));
  }
  check('ترتیب گزینه‌ها تصادفی است', positions.size >= 3);

  // ادمین
  store.delete('quiz_scores');
  await send(dm('/quiz', 4000, 'رضا'));
  sv = served();
  await send(answerCb(sv.right, 4000));
  await send(msg('/quizboard'));
  const qb = last('sendMessage');
  check('/quizboard: جدول و دکمهٔ انتشار', qb.body.text.includes('سارا') && JSON.stringify(qb.body.reply_markup).includes('qzpost'));
  await send(cb('qzpost'));
  const chPost = calls.find(c => c.method === 'sendMessage' && c.body.chat_id === '@chan');
  check('qzpost: جدول در کانال منتشر می‌شود', !!chPost && chPost.body.text.includes('سارا') && chPost.body.text.includes('/quiz'));
  await send(cb('qzpost', 12345));
  check('qzpost: غیرادمین رد می‌شود', !calls.some(c => c.method === 'sendMessage' && c.body.chat_id === '@chan'));
}

console.log(`\n${passed} تست Worker با موفقیت گذشت ✅`);