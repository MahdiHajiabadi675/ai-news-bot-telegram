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
  const method = String(url).split('/').pop();
  calls.push({ method, body });
  // پیام‌های تلگرام یک message_id برمی‌گردانند؛ /undo و /editlast به آن نیاز دارند
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
}

console.log(`\n${passed} تست Worker با موفقیت گذشت ✅`);