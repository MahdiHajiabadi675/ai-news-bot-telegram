/**
 * Cloudflare Worker — ربات تلگرام همیشه فعال
 * آمار و وضعیت کامل
 */

export default {
  async fetch(request, env, ctx) {
    const BOT_TOKEN = env.BOT_TOKEN;
    const CHANNEL_ID = env.CHANNEL_ID;
    const ADMIN_ID = Number(env.ADMIN_ID);
    const BASE = `https://api.telegram.org/bot${BOT_TOKEN}`;

    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, '') || '/';

    // «کلید مرده» فقط روی مسیرهای کم‌ترافیک بررسی می‌شود (RSS/API عمومی نه).
    if (ctx && typeof ctx.waitUntil === 'function' && (path === '/health' || path === '/webhook')) {
      ctx.waitUntil(checkHeartbeat(env));
    }

    // ── /rss.xml: خروجی RSS برای خوانندگان فید (بدون نیاز به تلگرام) ──
    if (path === '/rss.xml' && request.method === 'GET') {
      const published = await getJSON(env, 'published', []);
      const items = published.filter(p => p && p.published && !p.deleted).slice(-30).reverse();
      const origin = url.origin;
      const esc = escapeHtml;
      const entries = items.map(p => {
        const title = esc(p.title_fa || p.title || '');
        const link = safeLink(p.link) || origin;
        const date = p.published_at ? new Date(p.published_at).toUTCString() : new Date().toUTCString();
        const desc = esc(String(p.summary_fa || '').slice(0, 400));
        const guid = esc(p.id || p.hash || link);
        return `<item><title>${title}</title><link>${link}</link>` +
          `<guid isPermaLink="false">${guid}</guid><pubDate>${date}</pubDate>` +
          `<description>${desc}</description></item>`;
      }).join('');
      const xml = '<?xml version="1.0" encoding="UTF-8"?>' +
        `<rss version="2.0"><channel><title>AI News Bot</title><link>${origin}/</link>` +
        `<description>اخبار هوش مصنوعی</description>${entries}</channel></rss>`;
      return new Response(xml, {
        headers: { 'Content-Type': 'application/rss+xml; charset=utf-8', 'Cache-Control': 'public, max-age=300' },
      });
    }

    // ── /api/news: نسخهٔ JSON آخرین خبرها ──
    if (path === '/api/news' && request.method === 'GET') {
      // full=1 → فیلدهای غنی‌تر برای وب‌سایت (id، خلاصه، برچسب، شناسهٔ پست کانال) و سقف ۳۰۰.
      // CORS باز است (داده عمومی است) و پاسخ ۵ دقیقه در کش لبهٔ Cloudflare می‌ماند تا CPU/KV مصرف نشود.
      const full = url.searchParams.get('full') === '1';
      const limit = Math.min(Math.max(Number(url.searchParams.get('limit')) || 20, 1), full ? 300 : 100);
      const cacheKey = new Request(`${url.origin}/api/news?full=${full ? 1 : 0}&limit=${limit}`);
      const edge = typeof caches !== 'undefined' ? caches.default : null;
      if (edge) {
        const hit = await edge.match(cacheKey);
        if (hit) return hit;
      }
      const published = await getJSON(env, 'published', []);
      const items = published
        .filter(p => p && p.published && !p.deleted)
        .slice(-limit)
        .reverse()
        .map(p => {
          const base = {
            title: p.title_fa || p.title || '',
            source: p.source || '',
            link: p.link || '',
            category: p.category || '',
            published_at: p.published_at || null,
          };
          if (!full) return base;
          return {
            ...base,
            id: String(p.id || p.hash || '').slice(0, 64),
            summary: String(p.summary_fa || p.summary || '').slice(0, 600),
            tags: Array.isArray(p.hashtags) ? p.hashtags.slice(0, 6) : [],
            message_id: Number.isInteger(p.message_id) ? p.message_id : null,
          };
        });
      const payload = { count: items.length, items };
      if (full) payload.channel = String(CHANNEL_ID || '').startsWith('@') ? String(CHANNEL_ID).slice(1) : '';
      const resp = new Response(JSON.stringify(payload, null, full ? 0 : 2), {
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': 'public, max-age=300',
          'Access-Control-Allow-Origin': '*',
        },
      });
      if (edge && ctx && typeof ctx.waitUntil === 'function') ctx.waitUntil(edge.put(cacheKey, resp.clone()));
      return resp;
    }

    // ── /health: پایش ساده (برای UptimeRobot و انسان) ──
    // هیچ اطلاعات حساسی برنمی‌گرداند: فقط شمارنده‌ها و زمان آخرین اجرا.
    if (path === '/health' && (request.method === 'GET' || request.method === 'HEAD')) {
      const snap = await healthSnapshot(env);
      return new Response(JSON.stringify(snap, null, 2), {
        status: snap.ok ? 200 : 503,
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
      });
    }

    if (path === '/webhook' && request.method === 'POST') {
      const denial = webhookAuthError(request, env);
      if (denial) return new Response(denial, { status: 403 });

      let update;
      try {
        update = await request.json();
      } catch {
        return new Response('Bad Request', { status: 400 });
      }

      // ضدتکرار: تلگرام تا رسیدن پاسخ ۲۰۰ همان update را دوباره می‌فرستد و
      // بدون این چک، یک کلیک «تایید» می‌توانست دوبار پست کند.
      if (await isDuplicateUpdate(env, update)) {
        return new Response('OK');
      }

      if (update.callback_query) {
        await handleCallback(update.callback_query, env, BASE, CHANNEL_ID, ADMIN_ID);
      } else if (update.message) {
        await handleMessage(update.message, env, BASE, CHANNEL_ID, ADMIN_ID);
      } else if (update.message_reaction_count) {
        // 📊 واکنش‌های کانال (ناشناس، تجمیعی) — ذخیره برای /top و بوست دسته‌ها
        await recordReactionCount(env, update.message_reaction_count);
      }

      await rememberUpdate(env, update);
      return new Response('OK');
    }

    if (path === '/run' && request.method === 'POST') {
      const auth = request.headers.get('Authorization') || '';
      const token = auth.startsWith('Bearer ') ? auth.slice('Bearer '.length) : '';
      if (!env.WORKER_SECRET || !safeEqual(token, env.WORKER_SECRET)) {
        return jsonResponse({ ok: false, error: 'Unauthorized' }, 403);
      }

      // قفل سادهٔ اجرا: دو /run هم‌زمان نباید یک صف را دوبار منتشر کنند.
      // (KV سازگاری نهایی دارد؛ این قفل بهترین تلاش است، نه تضمین.)
      const lock = await getJSON(env, 'run_lock', {});
      if (lock && Number(lock.at) && Date.now() - Number(lock.at) < 5 * 60 * 1000) {
        return jsonResponse({ ok: false, error: 'already running' }, 409);
      }
      await saveJSON(env, 'run_lock', { at: Date.now() });
      try {
        return await handleRun(env, BASE, CHANNEL_ID, ADMIN_ID);
      } finally {
        await saveJSON(env, 'run_lock', {});
      }
    }

    return new Response('OK');
  },

  /**
   * اختیاری: اجرای ساعت‌به‌ساعتِ ورک‌فلوی گیت‌هاب از طریق Cloudflare.
   * فقط وقتی کار می‌کند که GITHUB_TOKEN و GITHUB_REPO در secrets تنظیم شده باشند.
   * (برای فعال‌سازی: Settings → Triggers → Cron = 17 * * * *)
   */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(checkHeartbeat(env));
    if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return;
    ctx.waitUntil(dispatchGithubWorkflow(env));
  }
};

// بنر منوی /start — داخل مخزن GitHub میزبانی می‌شود تا به هاست بیرونی وابسته نباشد.
const START_BANNER_URL = 'https://raw.githubusercontent.com/MahdiHajiabadi675/ai-news-bot-telegram/main/assets/banner.jpg?v=1';

// ───────────── احراز هویت وبهوک ─────────────
// ۱) اگر WEBHOOK_SECRET تنظیم شده باشد، هدر secret_token تلگرام باید دقیقاً همان باشد.
// ۲) اگر تنظیم نشده باشد، فقط درخواست‌هایی از رنج IP خود تلگرام پذیرفته می‌شوند؛
//    قبلاً در این حالت *هر* درخواستی قبول می‌شد و هر کسی می‌توانست update جعلی
//    بفرستد (مثلاً پاک‌کردن کل صف با callback_data «clra»).
// راه توصیه‌شده: WEBHOOK_SECRET بساز و setWebhook را با &secret_token=<مقدار> صدا بزن.
const TELEGRAM_CIDRS = ['149.154.160.0/20', '91.108.4.0/22'];

function ipv4ToInt(ip) {
  const parts = String(ip).trim().split('.');
  if (parts.length !== 4) return null;
  let out = 0;
  for (const p of parts) {
    if (!/^\d{1,3}$/.test(p)) return null;
    const n = Number(p);
    if (n > 255) return null;
    out = (out * 256) + n;
  }
  return out;
}

function inCidr(ip, cidr) {
  const [base, bitsRaw] = String(cidr).split('/');
  const bits = Number(bitsRaw);
  const a = ipv4ToInt(ip);
  const b = ipv4ToInt(base);
  if (a === null || b === null || !Number.isFinite(bits)) return false;
  const mask = bits === 0 ? 0 : (0xFFFFFFFF << (32 - bits)) >>> 0;
  return ((a & mask) >>> 0) === ((b & mask) >>> 0);
}

function isTelegramIp(ip) {
  if (!ip) return false;
  return TELEGRAM_CIDRS.some(cidr => inCidr(ip, cidr));
}

/** مقایسهٔ ثابت‌زمان رشته‌ها (جلوگیری از حملات زمان‌سنجی روی توکن) */
function safeEqual(a, b) {
  const x = new TextEncoder().encode(String(a ?? ''));
  const y = new TextEncoder().encode(String(b ?? ''));
  if (x.length !== y.length) return false;
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

function webhookAuthError(request, env) {
  const secret = String(env.WEBHOOK_SECRET || '').trim();
  if (secret) {
    const got = request.headers.get('X-Telegram-Bot-Api-Secret-Token') || '';
    return safeEqual(got, secret) ? null : 'Forbidden';
  }
  const ip = String(request.headers.get('CF-Connecting-IP') || '').trim();
  if (isTelegramIp(ip)) return null;
  console.error('[webhook] rejected: WEBHOOK_SECRET is not set and remote IP is not Telegram:', ip || '(none)');
  return 'Forbidden';
}

/** آیا این update قبلاً پردازش شده است؟ (تلگرام تحویل تکراری دارد) */
async function isDuplicateUpdate(env, update) {
  const uid = Number(update && update.update_id);
  if (!Number.isFinite(uid) || uid <= 0) return false;
  const state = await getJSON(env, 'state', {});
  return uid <= Number(state.webhook_last_update_id || 0);
}

async function rememberUpdate(env, update) {
  const uid = Number(update && update.update_id);
  if (!Number.isFinite(uid) || uid <= 0) return;
  await updateState(env, { webhook_last_update_id: uid });
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' }
  });
}

async function handleCallback(cb, env, BASE, CHANNEL_ID, ADMIN_ID) {
  // Never trust callback data without checking the Telegram user who clicked it.
  // دکمه‌های کوییز برای همهٔ کاربران است (قبل از دروازهٔ ادمین)
  if (String(cb.data || '').startsWith('qz:')) {
    await handleQuizCallback(cb, env, BASE, CHANNEL_ID);
    return;
  }
  if (Number(cb.from?.id) !== ADMIN_ID) {
    await answerCallback(BASE, cb.id, '⛔️ دسترسی ندارید', true);
    return;
  }
  const data = cb.data || '';
  const cbId = cb.id;
  const parts = data.split(':');
  const action = parts[0];
  const itemHash = parts[1] || '';

  // دکمه‌های منوی /start → همان دستورها را اجرا می‌کنند
  const menuCommands = ['status', 'pending', 'sources', 'feeds', 'runs', 'stats', 'clear', 'auto', 'top', 'weekly', 'backup', 'tips', 'dashboard', 'ask', 'quizboard', 'promo'];
  if (action === 'cmd' && menuCommands.includes(itemHash)) {
    await answerCallback(BASE, cbId);
    await handleMessage({ text: '/' + itemHash, chat: { id: ADMIN_ID } }, env, BASE, CHANNEL_ID, ADMIN_ID);
    return;
  }

  // دکمه‌های انتشار خودکار (auto_on / auto_off / auto:on / auto:off / ...)
  // قبلاً هیچ handlerی نداشتند و کلیک روی آن‌ها بی‌صدا نادیده گرفته می‌شد.
  const autoChoice = parseAutoChoice(data);
  if (autoChoice !== null) {
    await setAutoPublish(env, BASE, ADMIN_ID, cbId, autoChoice);
    return;
  }

  if (action === 'tipok' || action === 'tipno') {
    const tips = await getJSON(env, 'tips', []);
    const tip = tips.find(t => t && t.id === itemHash);
    if (!tip) {
      await answerCallback(BASE, cbId, '❌ سوژه پیدا نشد', true);
      return;
    }
    if (action === 'tipok' && tip.status === 'approved') {
      await answerCallback(BASE, cbId, 'ℹ️ قبلاً منتشر شده', true);
      return;
    }
    if (action === 'tipno' && tip.status === 'rejected') {
      await answerCallback(BASE, cbId, 'ℹ️ قبلاً رد شده', true);
      return;
    }
    if (action === 'tipok') {
      const res = await sendMessage(BASE, CHANNEL_ID, formatTipPost(tip));
      if (!res || !res.ok) {
        await answerCallback(BASE, cbId, '⚠️ انتشار نشد', true);
        return;
      }
      tip.status = 'approved';
    } else {
      tip.status = 'rejected';
    }
    tip.reviewed_at = new Date().toISOString();
    await saveJSON(env, 'tips', tips);
    await answerCallback(BASE, cbId, action === 'tipok' ? '✅ منتشر شد' : '❌ رد شد');
    if (cb.message && cb.message.message_id) {
      try {
        await telegramCall(BASE, 'editMessageReplyMarkup', {
          chat_id: ADMIN_ID, message_id: cb.message.message_id,
          reply_markup: { inline_keyboard: [] },
        });
      } catch (e) { /* نادیده */ }
    }
    return;
  }

  const pending = await getJSON(env, 'pending', []);
  const approved = await getJSON(env, 'approved', []);

  if (action === 'sched') {
    // ⏰ تایید + انتشار در ساعت آینده (callback: sched:<hours>:<id>)
    const hours = Math.max(1, Math.min(48, Number(parts[1]) || 2));
    const targetId = parts[2] || '';
    const idx = pending.findIndex(i => itemKey(i) === targetId);
    if (idx !== -1) {
      const item = pending.splice(idx, 1)[0];
      await recordDecision(env, item, 'approve');
      item.status = 'approved';
      item.admin_action = 'scheduled';
      item.schedule_at = new Date(Date.now() + hours * 3600 * 1000).toISOString();
      approved.push(item);
      await saveJSON(env, 'pending', pending);
      await saveJSON(env, 'approved', approved);
      await answerCallback(BASE, cbId, `⏰ برای ${hours} ساعت بعد زمان‌بندی شد`);
    } else {
      await answerCallback(BASE, cbId, '❌ خبر پیدا نشد', true);
    }
    return;
  }

  if (action === 'pin') {
    // 📌 تایید + انتشار فوری + سنجاق در کانال
    const idx = pending.findIndex(i => itemKey(i) === itemHash);
    if (idx !== -1) {
      const item = pending.splice(idx, 1)[0];
      await recordDecision(env, item, 'approve');
      item.status = 'approved';
      item.admin_action = 'pinned';
      item.pin = true;
      approved.push(item);
      await saveJSON(env, 'pending', pending);
      await saveJSON(env, 'approved', approved);
      await answerCallback(BASE, cbId, '📌 تایید شد — در حال انتشار و سنجاق...');
      await publishItem(env, BASE, CHANNEL_ID, ADMIN_ID, item);
    } else {
      await answerCallback(BASE, cbId, '❌ خبر پیدا نشد', true);
    }
    return;
  }

  if (action === 'approve') {
    const idx = pending.findIndex(i => itemKey(i) === itemHash);
    if (idx !== -1) {
      const item = pending.splice(idx, 1)[0];
      await recordDecision(env, item, 'approve');
      item.status = 'approved';
      item.admin_action = 'approved';
      approved.push(item);
      await saveJSON(env, 'pending', pending);
      await saveJSON(env, 'approved', approved);
      await answerCallback(BASE, cbId, '✅ تایید شد — در حال انتشار...');
      // انتشار فوری
      await publishItem(env, BASE, CHANNEL_ID, ADMIN_ID, item);
    } else {
      await answerCallback(BASE, cbId, '❌ خبر پیدا نشد (شاید قبلاً حذف شده)', true);
    }
  } else if (action === 'reject') {
    const rest = pending.filter(i => itemKey(i) !== itemHash);
    await saveJSON(env, 'pending', rest);
    const found = rest.length < pending.length;
    if (found) {
      await recordDecision(env, pending.find(i => itemKey(i) === itemHash), 'reject');
    }
    await answerCallback(BASE, cbId, found ? '❌ رد شد' : '❌ خبر پیدا نشد', !found);
  } else if (action === 'feature') {
    const idx = pending.findIndex(i => itemKey(i) === itemHash);
    if (idx !== -1) {
      const item = pending.splice(idx, 1)[0];
      await recordDecision(env, item, 'feature');
      item.status = 'approved';
      item.admin_action = 'featured';
      approved.push(item);
      await saveJSON(env, 'pending', pending);
      await saveJSON(env, 'approved', approved);
      await answerCallback(BASE, cbId, '⭐️ ویژه — در بالای صف انتشار');
    } else {
      await answerCallback(BASE, cbId, '❌ خبر پیدا نشد', true);
    }
  } else if (action === 'edit') {
    const item = pending.find(i => itemKey(i) === itemHash);
    if (item) {
      item._editing = true;
      await saveJSON(env, 'pending', pending);
      await sendMessage(BASE, ADMIN_ID, '✏️ متن ویرایش‌شده رو بفرست:');
      await answerCallback(BASE, cbId, '');
    } else {
      await answerCallback(BASE, cbId, '❌ خبر پیدا نشد', true);
    }
  } else if (action === 'clearq') {
    await answerCallback(BASE, cbId, '');
    await askClearQueue(env, BASE, ADMIN_ID);
  } else if (action === 'clrp' || action === 'clra') {
    // پاک‌کردن صف: تصمیم «خنثی» است و در اعتبار منابع ثبت نمی‌شود
    const includeApproved = action === 'clra';
    const nP = pending.length;
    const nA = includeApproved ? approved.length : 0;
    await saveJSON(env, 'pending', []);
    if (includeApproved) await saveJSON(env, 'approved', []);
    await answerCallback(BASE, cbId, '🗑 پاک شد');
    await sendMessage(BASE, ADMIN_ID,
      `🗑 ${nP} خبر در انتظار تایید` + (includeApproved ? ` و ${nA} خبر آماده انتشار` : '') + ' پاک شد.');
  } else if (action === 'clrx') {
    await answerCallback(BASE, cbId, 'انصراف داده شد');
  } else if (action === 'undo_yes') {
    await answerCallback(BASE, cbId, '');
    await doUndo(env, BASE, CHANNEL_ID, ADMIN_ID);
  } else if (action === 'undo_no') {
    await answerCallback(BASE, cbId, 'انصراف داده شد');
  } else if (action === 'qzpost') {
    const off = (Number(env.DISPLAY_TZ_OFFSET_MIN) || 210) * 60000;
    const txt = leaderboardText(normScores(await getJSON(env, 'quiz_scores', {})), quizWeekId(Date.now(), off));
    const res = await sendMessage(BASE, CHANNEL_ID, txt.replace(/\n<i>.*<\/i>$/s, '\n<i>شرکت در کوییز: در چت ربات /quiz بزن.</i>'));
    await answerCallback(BASE, cbId, res && res.ok ? '📣 در کانال منتشر شد' : '⚠️ ارسال نشد', !(res && res.ok));
  } else if (action === 'pause') {
    await updateState(env, { paused: true });
    await answerCallback(BASE, cbId, '⏸️ متوقف');
  } else if (action === 'resume') {
    await updateState(env, { paused: false });
    await answerCallback(BASE, cbId, '▶️ فعال');
  } else {
    // دکمهٔ قدیمی/ناشناس: به‌جای سکوت، به کاربر بازخورد بده
    await answerCallback(BASE, cbId, 'این دکمه دیگر فعال نیست', true);
  }
}

// ───────────── 🔎 /ask: جست‌وجوی آرشیو (بدون مدل، بدون هزینه) ─────────────

const ASK_STOP = new Set(['و', 'در', 'به', 'از', 'که', 'را', 'این', 'آن', 'با', 'برای', 'یا', 'خبر', 'اخبار', 'درباره', 'دربارهٔ', 'چیه', 'چیست', 'the', 'a', 'an', 'of', 'in', 'on', 'about', 'for', 'and', 'to', 'is']);

/** یکسان‌سازی متن فارسی/انگلیسی برای جست‌وجو: ی/ک عربی، نیم‌فاصله، ارقام، حروف بزرگ، اعراب. */
function normSearch(s) {
  return String(s ?? '').toLowerCase()
    .replace(/[يى]/g, 'ی').replace(/ك/g, 'ک')
    .replace(/[‌‏‎]/g, ' ')
    .replace(/[ً-ٰٟ]/g, '')
    .replace(/[۰-۹]/g, d => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(d)))
    .replace(/[٠-٩]/g, d => String('٠١٢٣٤٥٦٧٨٩'.indexOf(d)));
}

function searchTokens(q) {
  const out = [];
  for (const t of normSearch(q).split(/[^\p{L}\p{N}]+/u)) {
    if (!t || ASK_STOP.has(t)) continue;
    if (t.length < 2 && !/\d/.test(t)) continue;
    if (!out.includes(t)) out.push(t);
  }
  return out.slice(0, 6);
}

/**
 * جست‌وجوی متنی در پست‌های منتشرشده. امتیاز: عنوان ۳، برچسب/دسته/منبع ۲، خلاصه ۱ برای هر کلمه؛
 * حداقل نصف کلمه‌ها باید پیدا شوند. هم‌امتیازها: جدیدتر اول. (آخرین ۶۰۰ پست؛ سقف CPU رایگان Worker.)
 */
function searchArchive(published, query, limit = 5) {
  const tokens = searchTokens(query);
  if (!tokens.length) return { tokens, hits: [] };
  const items = (Array.isArray(published) ? published : [])
    .filter(p => p && p.published && !p.deleted).slice(-600);
  const need = Math.max(1, Math.ceil(tokens.length / 2));
  const hits = [];
  items.forEach((p, idx) => {
    const title = normSearch(`${p.title_fa || ''} ${p.title || ''}`);
    const meta = normSearch(`${(p.hashtags || []).join(' ')} ${p.category || ''} ${p.source || ''}`);
    const body = normSearch(p.summary_fa || p.summary || '');
    let score = 0, found = 0;
    for (const t of tokens) {
      const sc = (title.includes(t) ? 3 : 0) + (meta.includes(t) ? 2 : 0) + (body.includes(t) ? 1 : 0);
      if (sc) { found++; score += sc; }
    }
    if (found >= need) hits.push({ p, score, idx });
  });
  hits.sort((a, b) => b.score - a.score || b.idx - a.idx);
  return { tokens, hits: hits.slice(0, limit).map(h => h.p) };
}

function askReport(query, result, env, CHANNEL_ID, now = Date.now()) {
  const handle = String(CHANNEL_ID || '').startsWith('@') ? String(CHANNEL_ID).slice(1) : '';
  if (!result.tokens.length) {
    return '🔎 <b>جست‌وجو در آرشیو</b>\n\nبعد از دستور، کلمهٔ مورد نظر را بنویس. مثال:\n' +
      '<code>/ask OpenAI</code>\n<code>/ask مدل متن‌باز</code>';
  }
  if (!result.hits.length) {
    return `🔎 برای «${escapeHtml(clip(query, 60))}» چیزی در آرشیو پیدا نشد.\n\n` +
      'کلمهٔ کوتاه‌تر یا نام شرکت/مدل را امتحان کن (مثلاً <code>/ask Gemini</code>).';
  }
  const lines = [`🔎 <b>نتایج برای «${escapeHtml(clip(query, 60))}»</b>\n`];
  result.hits.forEach((p, i) => {
    const title = escapeHtml(clip(p.title_fa || p.title || '', 90));
    const link = (handle && p.message_id) ? `https://t.me/${handle}/${p.message_id}` : safeLink(p.link);
    const when = p.published_at ? ` — ${agoText(p.published_at, now)}` : '';
    lines.push(`${i + 1}. ${link ? `<a href="${link}">${title}</a>` : title}${when}`);
  });
  return lines.join('\n');
}

/** سهمیهٔ /ask برای کاربران عادی: ساعتی برای هر نفر + سقف روزانهٔ کل (برای حفظ سقف نوشتن KV). */
async function askAllowed(env, chatId, now = Date.now()) {
  const perHour = Math.max(1, Number(env.ASK_MAX_PER_HOUR) || 10);
  const perDay = Math.max(10, Number(env.ASK_MAX_PER_DAY) || 200);
  const rates = await getJSON(env, 'ask_rate', {});
  const day = new Date(now).toISOString().slice(0, 10);
  const total = rates._day && rates._day.d === day ? Number(rates._day.n) || 0 : 0;
  if (total >= perDay) return { ok: false, why: 'day' };
  const mine = rates[String(chatId)];
  const used = mine && now - Date.parse(mine.at) < 3600000 ? Number(mine.count) || 0 : 0;
  if (used >= perHour) return { ok: false, why: 'hour', perHour };
  const next = { _day: { d: day, n: total + 1 } };
  for (const [k, v] of Object.entries(rates)) {      // فقط ورودی‌های داخل پنجرهٔ ساعت می‌مانند (رشد بی‌پایان ندارد)
    if (k !== '_day' && v && now - Date.parse(v.at) < 3600000) next[k] = v;
  }
  next[String(chatId)] = { at: mine && used ? mine.at : new Date(now).toISOString(), count: used + 1 };
  await saveJSON(env, 'ask_rate', next);
  return { ok: true };
}

async function handleAsk(text, env, BASE, chatId, CHANNEL_ID, isAdmin) {
  const query = text.replace(/^\/ask(@\w+)?\s*/i, '').replace(/\s+/g, ' ').trim().slice(0, 120);
  if (query && !isAdmin) {
    const gate = await askAllowed(env, chatId);
    if (!gate.ok) {
      await sendMessage(BASE, chatId, gate.why === 'day'
        ? '⏳ امروز سهمیهٔ جست‌وجو پر شده؛ فردا دوباره امتحان کن.'
        : `⏳ در هر ساعت حداکثر ${gate.perHour} جست‌وجو. کمی بعد دوباره امتحان کن.`);
      return;
    }
  }
  const published = query ? await getJSON(env, 'published', []) : [];
  const result = searchArchive(published, query);
  await sendLong(BASE, chatId, askReport(query, result, env, CHANNEL_ID));
}

// ───────────── 🧠 کوییز امتیازی در چت خصوصی (/quiz /me /leaderboard) ─────────────
// نظرسنجی کانال در تلگرام ناشناس است و پاسخ هر نفر قابل ثبت نیست؛ پس کوییز امتیازی با دکمه‌های
// شیشه‌ای در چت خصوصی انجام می‌شود. بانک سؤال را pipeline در KV (quiz_bank) می‌گذارد.
// سقف نوشتن KV: هر سؤال = یک نوشتن برای ارسال + یک برای پاسخ؛ تعداد ارسال روزانهٔ کل محدود است.

const QUIZ_POINTS = 10;
const QUIZ_LABELS = ['الف', 'ب', 'ج', 'د'];

function quizCfg(env) {
  return {
    perUser: Math.max(1, Number(env.QUIZ_MAX_PER_DAY) || 10),
    perDayTotal: Math.max(10, Number(env.QUIZ_MAX_SENDS_PER_DAY) || 150),
  };
}

/** شناسهٔ هفته = تاریخ آخرین شنبه (به وقت نمایش)؛ هفتهٔ فارسی از شنبه شروع می‌شود. */
function quizWeekId(now, offMs) {
  const d = new Date(now + offMs);
  const back = (d.getUTCDay() + 1) % 7;   // شنبه=۰ ... جمعه=۶
  return new Date(d.getTime() - back * 86400000).toISOString().slice(0, 10);
}

function normScores(raw) {
  const sc = raw && typeof raw === 'object' ? raw : {};
  if (!sc.users || typeof sc.users !== 'object') sc.users = {};
  return sc;
}

async function saveScores(env, sc) {
  const ids = Object.keys(sc.users);
  if (ids.length > 1500) {                // رشد بی‌پایان ندارد: غیرفعال‌ترین‌ها حذف می‌شوند
    ids.sort((a, b) => String(sc.users[a].last || '').localeCompare(String(sc.users[b].last || '')));
    for (const id of ids.slice(0, ids.length - 1500)) delete sc.users[id];
  }
  await saveJSON(env, 'quiz_scores', sc);
}

function quizUser(sc, from, now) {
  const id = String(from.id);
  const u = sc.users[id] || { t: 0, ok: 0, tot: 0, seen: [], ans: [] };
  u.n = String(from.first_name || from.username || 'کاربر').slice(0, 20);
  u.last = new Date(now).toISOString();
  sc.users[id] = u;
  return u;
}

function quizWeekly(u, weekId) {
  if (!u.w || u.w.id !== weekId) u.w = { id: weekId, pts: 0, ok: 0 };
  return u.w;
}

function quizDisplayName(u) {
  return u.hide ? 'ناشناس' : escapeHtml(u.n || 'کاربر');
}

async function sendQuizQuestion(env, BASE, chatId, from, now = Date.now()) {
  const bank = await getJSON(env, 'quiz_bank', []);
  if (!Array.isArray(bank) || !bank.length) {
    await sendMessage(BASE, chatId, '🧠 بانک سؤال هنوز آماده نیست؛ بعد از اجرای بعدی ربات دوباره امتحان کن.');
    return;
  }
  const cfg = quizCfg(env);
  const sc = normScores(await getJSON(env, 'quiz_scores', {}));
  const day = new Date(now).toISOString().slice(0, 10);
  if (!sc._meta || sc._meta.d !== day) sc._meta = { d: day, n: 0 };
  if (sc._meta.n >= cfg.perDayTotal) {
    await sendMessage(BASE, chatId, '⏳ ظرفیت کوییز امروز پر شده؛ فردا دوباره بیا.');
    return;
  }
  const u = quizUser(sc, from, now);
  if (!u.dd || u.dd.d !== day) u.dd = { d: day, n: 0 };
  if (u.dd.n >= cfg.perUser) {
    await sendMessage(BASE, chatId, `✅ امروز ${cfg.perUser} سؤال را پاسخ دادی. فردا ادامه بده!\nامتیازت: <code>/me</code> · جدول: <code>/leaderboard</code>`);
    return;
  }
  let pool = bank.filter(q => q && !u.seen.includes(q.id));
  if (!pool.length) { u.seen = []; pool = bank.filter(Boolean); }   // همهٔ سؤال‌ها را دیده: دور تازه
  const q = pool[Math.floor(Math.random() * pool.length)];
  const perm = [0, 1, 2, 3].slice(0, q.o.length);
  for (let i = perm.length - 1; i > 0; i--) {   // ترتیب نمایش تصادفی (در بانک پاسخ درست معمولاً گزینهٔ دوم است)
    const j = Math.floor(Math.random() * (i + 1));
    [perm[i], perm[j]] = [perm[j], perm[i]];
  }
  u.seen.push(q.id); u.seen = u.seen.slice(-60);
  u.dd.n++; sc._meta.n++;
  await saveScores(env, sc);
  const body = perm.map((orig, i) => `${QUIZ_LABELS[i]}) ${escapeHtml(q.o[orig])}`).join('\n');
  await sendMessage(BASE, chatId,
    `🧠 <b>کوییز</b> (${u.dd.n}/${cfg.perUser} امروز)\n\n${escapeHtml(q.q)}\n\n${body}`,
    { inline_keyboard: [perm.map((orig, i) => ({ text: QUIZ_LABELS[i], callback_data: `qz:${q.id}:${orig}` }))] });
}

async function handleQuizCallback(cb, env, BASE, CHANNEL_ID, now = Date.now()) {
  const cbId = cb.id;
  const chatId = cb.message && cb.message.chat && cb.message.chat.id;
  const parts = String(cb.data || '').split(':');
  if (parts[1] === 'next') {
    await answerCallback(BASE, cbId);
    if (chatId) await sendQuizQuestion(env, BASE, chatId, cb.from || {}, now);
    return;
  }
  const qid = parts[1];
  const idx = Number(parts[2]);
  const bank = await getJSON(env, 'quiz_bank', []);
  const q = Array.isArray(bank) ? bank.find(x => x && x.id === qid) : null;
  if (!q || !Number.isInteger(idx) || idx < 0 || idx >= q.o.length) {
    await answerCallback(BASE, cbId, 'این سؤال دیگر در دسترس نیست', true);
    return;
  }
  const off = (Number(env.DISPLAY_TZ_OFFSET_MIN) || 210) * 60000;
  const sc = normScores(await getJSON(env, 'quiz_scores', {}));
  const u = quizUser(sc, cb.from || {}, now);
  if (!u.seen.includes(qid)) {            // فقط سؤالی که همین ربات برای خود او فرستاده امتیاز دارد
    await answerCallback(BASE, cbId, 'این سؤال مال تو نبود؛ /quiz بزن', true);
    return;
  }
  if (u.ans.includes(qid)) {
    await answerCallback(BASE, cbId, 'قبلاً به این سؤال پاسخ داده‌ای', true);
    return;
  }
  const correct = idx === q.c;
  const w = quizWeekly(u, quizWeekId(now, off));
  u.tot++; u.ans.push(qid); u.ans = u.ans.slice(-80);
  if (correct) { u.t += QUIZ_POINTS; u.ok++; w.pts += QUIZ_POINTS; w.ok++; }
  await saveScores(env, sc);
  await answerCallback(BASE, cbId, correct ? '✅ درست!' : '❌ نادرست');
  if (cb.message && cb.message.message_id) {
    await telegramCall(BASE, 'editMessageText', {
      chat_id: chatId, message_id: cb.message.message_id, parse_mode: 'HTML',
      text: `${correct ? `✅ <b>آفرین! +${QUIZ_POINTS} امتیاز</b>` : '❌ <b>این نبود</b>'}\n\n${escapeHtml(q.q)}\n\n` +
        `✔️ پاسخ درست: ${escapeHtml(q.o[q.c])}\n\n💡 ${escapeHtml(q.e || '')}\n\n` +
        `⭐️ امتیاز تو: <b>${u.t}</b> | این هفته: <b>${w.pts}</b>`,
      reply_markup: { inline_keyboard: [[{ text: '➡️ سؤال بعدی', callback_data: 'qz:next' }]] },
    });
  }
}

function quizRank(sc, id, key, weekId) {
  const rows = Object.entries(sc.users)
    .map(([k, u]) => [k, key === 'w' ? (u.w && u.w.id === weekId ? u.w.pts : 0) : (u.t || 0)])
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);
  const i = rows.findIndex(([k]) => k === String(id));
  return i < 0 ? null : i + 1;
}

function leaderboardText(sc, weekId) {
  const medals = ['🥇', '🥈', '🥉'];
  const top = (key, n) => Object.values(sc.users)
    .map(u => ({ u, v: key === 'w' ? (u.w && u.w.id === weekId ? u.w.pts : 0) : (u.t || 0) }))
    .filter(x => x.v > 0).sort((a, b) => b.v - a.v).slice(0, n);
  const fmt = (rows) => rows.map((x, i) => `${medals[i] || `${i + 1}.`} ${quizDisplayName(x.u)} — <b>${x.v}</b>`).join('\n');
  const wk = top('w', 10), all = top('t', 5);
  if (!wk.length && !all.length) return '🏆 هنوز کسی امتیاز نگرفته. با <code>/quiz</code> اولین نفر باش!';
  return '🏆 <b>جدول کوییز</b>\n\n' +
    (wk.length ? `📅 <b>این هفته</b>\n${fmt(wk)}\n\n` : '') +
    (all.length ? `⭐️ <b>همیشگی</b>\n${fmt(all)}\n\n` : '') +
    '<i>شرکت: ربات را باز کن و /quiz بزن. نام کوچکت نمایش داده می‌شود؛ برای ناشناس‌شدن /hide.</i>';
}

async function handleQuizCommand(text, env, BASE, chatId, from, now = Date.now()) {
  const off = (Number(env.DISPLAY_TZ_OFFSET_MIN) || 210) * 60000;
  const weekId = quizWeekId(now, off);
  if (/^\/quiz\b/i.test(text)) {
    await sendQuizQuestion(env, BASE, chatId, from, now);
  } else if (/^\/leaderboard\b/i.test(text)) {
    await sendLong(BASE, chatId, leaderboardText(normScores(await getJSON(env, 'quiz_scores', {})), weekId));
  } else if (/^\/me\b/i.test(text)) {
    const sc = normScores(await getJSON(env, 'quiz_scores', {}));
    const u = sc.users[String(from.id)];
    if (!u || !u.tot) {
      await sendMessage(BASE, chatId, 'هنوز به سؤالی پاسخ نداده‌ای. با <code>/quiz</code> شروع کن!');
      return;
    }
    const wp = u.w && u.w.id === weekId ? u.w.pts : 0;
    const rw = quizRank(sc, from.id, 'w', weekId), ra = quizRank(sc, from.id, 't', weekId);
    await sendMessage(BASE, chatId,
      `🙋 <b>${escapeHtml(u.n || 'تو')}</b>${u.hide ? ' (ناشناس در جدول)' : ''}\n\n` +
      `⭐️ امتیاز کل: <b>${u.t}</b>${ra ? ` (رتبهٔ ${ra})` : ''}\n` +
      `📅 این هفته: <b>${wp}</b>${rw ? ` (رتبهٔ ${rw})` : ''}\n` +
      `🎯 دقت: ${u.ok}/${u.tot} (${Math.round(100 * u.ok / u.tot)}٪)`);
  } else if (/^\/hide\b/i.test(text)) {
    const sc = normScores(await getJSON(env, 'quiz_scores', {}));
    const u = quizUser(sc, from, now);
    u.hide = !u.hide;
    await saveScores(env, sc);
    await sendMessage(BASE, chatId, u.hide ? '🙈 از این به بعد در جدول «ناشناس» نمایش داده می‌شوی.' : '👀 نام کوچکت دوباره در جدول نمایش داده می‌شود.');
  }
}

async function handlePublicMessage(msg, env, BASE, CHANNEL_ID, ADMIN_ID) {
  const text = (msg.text || '').trim();
  const chat = msg.chat || {};
  const chatId = chat.id;
  if (!chatId || chat.type !== 'private') return;
  const from = msg.from || {};
  const user = [from.first_name || '', from.username ? '@' + from.username : '']
    .filter(Boolean).join(' ').slice(0, 60);

  if (/^\/(start|help)\b/i.test(text)) {
    await sendMessage(BASE, chatId,
      '🤖 <b>ربات خبری AI</b>\n\n' +
      '• <code>/subscribe</code> — دریافت خلاصهٔ صبح در چت خصوصی\n' +
      '• <code>/unsubscribe</code> — قطع اشتراک\n' +
      '• <code>/ask کلمه</code> — جست‌وجو در آرشیو کانال\n' +
      '• <code>/quiz</code> — کوییز امتیازی · <code>/me</code> امتیاز من · <code>/leaderboard</code> جدول\n' +
      '• <code>/tip متن سوژه</code> — فرستادن خبر/لینک برای بررسی\n\n' +
      '<i>کانال خبری جدا از این چت است.</i>');
    return;
  }

  if (/^\/ask\b/i.test(text)) {
    await handleAsk(text, env, BASE, chatId, CHANNEL_ID, false);
    return;
  }

  if (/^\/(quiz|me|leaderboard|hide)\b/i.test(text)) {
    await handleQuizCommand(text, env, BASE, chatId, from);
    return;
  }

  if (/^\/subscribe\b/i.test(text)) {
    const subs = await getJSON(env, 'subscribers', []);
    const cap = Math.min(500, Math.max(1, Number(env.DM_DIGEST_MAX) || 200));
    if (subs.some(s => String(s.chat_id) === String(chatId))) {
      await sendMessage(BASE, chatId, 'ℹ️ از قبل مشترک بودی. برای قطع: <code>/unsubscribe</code>');
    } else if (subs.length >= cap) {
      await sendMessage(BASE, chatId, '⚠️ ظرفیت اشتراک پر شده است.');
    } else {
      subs.push({ chat_id: chatId, user, at: new Date().toISOString() });
      await saveJSON(env, 'subscribers', subs);
      await sendMessage(BASE, chatId, '✅ اشتراک فعال شد. هر صبح خلاصهٔ «صبحانهٔ AI» را همین‌جا می‌گیری.');
    }
    return;
  }

  if (/^\/(unsubscribe|stop)\b/i.test(text)) {
    const subs = await getJSON(env, 'subscribers', []);
    const next = subs.filter(s => String(s.chat_id) !== String(chatId));
    await saveJSON(env, 'subscribers', next);
    await sendMessage(BASE, chatId,
      next.length === subs.length ? 'ℹ️ اشتراکی ثبت نشده بود.' : '✅ اشتراک قطع شد.');
    return;
  }

  if (/^\/tip\b/i.test(text)) {
    let body = text.replace(/^\/tip\s*/i, '').replace(/\s+/g, ' ').trim();
    if (body.length < 12) {
      await sendMessage(BASE, chatId, 'متن کوتاه است (حداقل ۱۲ کاراکتر). مثال:\n<code>/tip OpenAI مدل جدیدی معرفی کرد</code>');
      return;
    }
    if (body.length > 600) body = body.slice(0, 600);
    const rates = await getJSON(env, 'tip_rate', {});
    const rateKey = String(chatId);
    const rawRate = rates[rateKey];
    const rateAt = rawRate && typeof rawRate === 'object' ? rawRate.at : rawRate;
    const rateCount = rawRate && typeof rawRate === 'object' ? Number(rawRate.count) || 0 : 1;
    const limit = Math.max(1, Number(env.TIPS_MAX_PER_HOUR) || 1);
    const inWindow = rateAt && Date.now() - Date.parse(rateAt) < 3600 * 1000;
    const used = inWindow ? rateCount : 0;
    if (used >= limit) {
      await sendMessage(BASE, chatId,
        `⏳ سهمیهٔ این ساعت پر شده (حداکثر ${limit} سوژه در ساعت). یک ساعت دیگر دوباره تلاش کن.`);
      return;
    }
    const tips = await getJSON(env, 'tips', []);
    const tip = {
      id: `tip-${Date.now()}-${chatId}`, chat_id: chatId, user, text: body,
      at: new Date().toISOString(), status: 'new',
    };
    tips.push(tip);
    await saveJSON(env, 'tips', tips.slice(-200));
    rates[rateKey] = { at: new Date().toISOString(), count: used + 1 };
    await saveJSON(env, 'tip_rate', rates);
    await sendMessage(BASE, chatId, '✅ سوژه ثبت شد. اگر تایید شود، با نام «از مخاطبان» منتشر می‌شود.');
    if (ADMIN_ID) {
      await sendMessage(BASE, ADMIN_ID,
        `📣 <b>سوژهٔ تازه</b>\nاز: ${escapeHtml(user || String(chatId))}\n\n${escapeHtml(body)}`,
        { inline_keyboard: [[
          { text: '✅ انتشار', callback_data: `tipok:${tip.id}` },
          { text: '❌ رد', callback_data: `tipno:${tip.id}` },
        ]] });
    }
    return;
  }

  await sendMessage(BASE, chatId, 'دستورها: /ask کلمه · /quiz · /me · /leaderboard · /subscribe · /unsubscribe · /tip متن سوژه');
}

async function handleMessage(msg, env, BASE, CHANNEL_ID, ADMIN_ID) {
  const text = (msg.text || '').trim();
  const chatId = msg.chat.id;

  if (chatId !== ADMIN_ID) {
    await handlePublicMessage(msg, env, BASE, CHANNEL_ID, ADMIN_ID);
    return;
  }

  const pending = await getJSON(env, 'pending', []);
  const approved = await getJSON(env, 'approved', []);
  const state = await getJSON(env, 'state', {
    paused: false,
    auto_publish_enabled: true,
    daily_post_count: 0,
    daily_post_date: null,
    current_stage: 'idle'
  });
  migrateState(state);

  // اگر هنگام ویرایش دستوری رسید، حالت ویرایش لغو شود
  if (text.startsWith('/')) {
    let cancelled = false;
    for (const item of pending) {
      if (item._editing) { item._editing = false; cancelled = true; }
    }
    if (cancelled) await saveJSON(env, 'pending', pending);
  }

  // Check if editing — دستورها (شروع با /) هرگز به‌عنوان متن ویرایش ذخیره نشوند
  for (const item of pending) {
    if (item._editing && !text.startsWith('/')) {
      item.summary_fa = text;
      item._editing = false;
      item._edited = true;
      await saveJSON(env, 'pending', pending);
      await sendMessage(BASE, ADMIN_ID, '✅ ویرایش انجام شد');
      return;
    }
  }

  // ویرایش خلاصهٔ پست منتشرشده (بعد از /editlast)
  if (state._editing_pub && text && !text.startsWith('/')) {
    await applyPublishedEdit(env, BASE, CHANNEL_ID, ADMIN_ID, state._editing_pub, text);
    return;
  }

  if (text === '/start') {
    const active = state.paused ? '⏸️ متوقف' : '▶️ فعال';
    // لینک آرشیو موضوعی فقط وقتی آیدی کانال یوزرنیم باشد
    const channelHandle = String(CHANNEL_ID || '').startsWith('@') ? String(CHANNEL_ID).slice(1) : '';
    const autoOn = state.auto_publish_enabled !== false;
    const stageLabel = {
      idle: 'در انتظار اجرا',
      collecting: 'در حال جمع‌آوری',
      analyzing: 'در حال تحلیل',
      publishing: 'در حال انتشار',
      waiting: 'در انتظار تایید',
      error: 'خطا'
    }[state.current_stage || 'idle'] || 'در انتظار اجرا';

    await sendStartMessage(BASE, ADMIN_ID,
      '🤖 <b>ربات مدیریت اخبار AI</b>\n' +
      '<i>جمع‌آوری، ترجمه و انتشار هوشمند</i>\n\n' +
      '━━━━━━━━━━━━━━\n' +
      '📊 <b>وضعیت لحظه‌ای</b>\n' +
      `├ وضعیت: ${active}\n` +
      `├ انتشار خودکار: ${autoOn ? 'روشن ✅' : 'خاموش ❌'}\n` +
      `├ مرحله: ${stageLabel}\n` +
      `├ 📥 در انتظار تایید: <b>${faNum(pending.length)}</b>\n` +
      `└ 📤 آماده انتشار: <b>${faNum(approved.length)}</b>\n\n` +
      '━━━━━━━━━━━━━━\n' +
      '⌨️ <b>دستورهای پرکاربرد</b>\n' +
      '<code>/status</code> · <code>/pending</code> · <code>/stats</code> — وضعیت، صف تایید، آمار\n' +
      '<code>/auto</code> · <code>/edu</code> · <code>/promo</code> — خودکار، آموزشی، تبلیغ سایت\n' +
      '<code>/pause</code> · <code>/resume</code> · <code>/clear</code> — توقف، ادامه، پاک‌کردن صف\n' +
      '<code>/undo</code> · <code>/editlast</code> · <code>/welcome</code> — حذف/ویرایش آخرین پست، خوش‌آمد\n' +
      '<code>/sources</code> · <code>/feeds</code> · <code>/runs</code> — منابع، فیدها، اجراها\n' +
      '<code>/dashboard</code> · <code>/top</code> · <code>/weekly</code> · <code>/quizboard</code> — داشبورد، پربازدید، هفتگی، جدول کوییز\n' +
      '<code>/ask</code> کلمه — جست‌وجو در آرشیو\n\n' +
      '━━━━━━━━━━━━━━\n' +
      '💡 <i>از دکمه‌های زیر هم می‌توانی استفاده کنی.</i>',
      {
        inline_keyboard: [
          [
            { text: '📊 وضعیت', callback_data: 'cmd:status' },
            { text: '📋 صف تایید', callback_data: 'cmd:pending' }
          ],
          [
            { text: '📡 منابع', callback_data: 'cmd:sources' },
            { text: '🩺 فیدها', callback_data: 'cmd:feeds' }
          ],
          [
            { text: '📈 آمار', callback_data: 'cmd:stats' },
            { text: '🗞 اجراها', callback_data: 'cmd:runs' }
          ],
          [
            { text: '📟 داشبورد', callback_data: 'cmd:dashboard' },
            { text: '🔎 جست‌وجو', callback_data: 'cmd:ask' }
          ],
          [
            { text: '🏆 جدول کوییز', callback_data: 'cmd:quizboard' },
            { text: '📣 تبلیغ سایت', callback_data: 'cmd:promo' }
          ],
          [
            { text: '👁 پربازدید', callback_data: 'cmd:top' },
            { text: '🗓 هفتگی', callback_data: 'cmd:weekly' }
          ],
          [
            { text: '📣 سوژه‌ها', callback_data: 'cmd:tips' },
            { text: '💾 پشتیبان', callback_data: 'cmd:backup' }
          ],
          [
            { text: '🤖 روشن', callback_data: 'auto_on' },
            { text: '⏸ خاموش', callback_data: 'auto_off' }
          ],
          [
            { text: '🧹 پاک‌کردن صف', callback_data: 'cmd:clear' }
          ],
          ...(channelHandle ? [[{
            text: '🗂 آرشیو موضوعی',
            url: `https://t.me/s/${channelHandle}?q=%23هوش_مصنوعی`,
          }]] : [])
        ]
      },
      env.START_BANNER_URL || START_BANNER_URL
    );
  } else if (text === '/status') {
    const stage = state.current_stage || 'idle';
    const stageEmoji = {
      idle: '💤',
      collecting: '📡',
      analyzing: '🧠',
      publishing: '📤',
      waiting: '⏳',
      error: '🔴'
    }[stage] || '❓';

    const paused = state.paused ? '⏸️ متوقف' : '▶️ فعال';
    const auto = (state.auto_publish_enabled === undefined ? true : state.auto_publish_enabled) ? 'روشن ✅' : 'خاموش ❌';
    const today = new Date().toISOString().slice(0, 10);
    const dailyCount = state.daily_post_date === today ? (state.daily_post_count || 0) : 0;
    const maxPosts = Number(env.MAX_POSTS_PER_RUN) || 15;

    // آمار کلی
    const published = await getJSON(env, 'published', []);
    const totalPublished = published.filter(p => p.published).length;
    const totalRejected = published.filter(p => !p.published).length;

    // منابع بر اساس تعداد
    const sourceCounts = {};
    for (const p of published) {
      if (p.published) {
        sourceCounts[p.source] = (sourceCounts[p.source] || 0) + 1;
      }
    }
    const topSources = Object.entries(sourceCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([name, count]) => `${name}: ${count}`)
      .join('\n');

    // برای عیب‌یابی: آخرین جمع‌آوری و تعداد خبرهای دیده‌شده
    const lastCollect = state.last_collect_time
      ? String(state.last_collect_time).slice(0, 16).replace('T', ' ')
      : '—';
    const seenList = await getJSON(env, 'seen', []);

    await sendMessage(BASE, ADMIN_ID,
      `📊 <b>وضعیت ربات</b>\n\n` +
      `${stageEmoji} مرحله فعلی: <b>${stage}</b>\n` +
      `━━━━━━━━━━━━━━\n` +
      `وضعیت: ${paused}\n` +
      `انتشار خودکار: ${auto}\n` +
      `━━━━━━━━━━━━━━\n` +
      `📰 این راند: ${dailyCount}/${maxPosts}\n` +
      `📋 در انتظار تایید: ${pending.length}\n` +
      `✅ آماده انتشار: ${approved.length}\n` +
      `━━━━━━━━━━━━━━\n` +
      `📈 کل منتشر شده: ${totalPublished}\n` +
      `❌ کل رد شده: ${totalRejected}\n` +
      `━━━━━━━━━━━━━━\n` +
      `🕐 آخرین جمع‌آوری: ${lastCollect}\n` +
      `🔎 خبرهای دیده‌شده: ${Array.isArray(seenList) ? seenList.length : 0}\n` +
      (state.last_error ? `⚠️ خطای آخر: ${state.last_error}\n` : '') +
      (topSources ? `━━━━━━━━━━━━━━\n🏆 برترین منابع:\n${topSources}` : ''),
      { inline_keyboard: [[
        { text: '🤖 روشن', callback_data: 'auto_on' },
        { text: '⏸ خاموش', callback_data: 'auto_off' }
      ]] }
    );
  } else if (text === '/pending') {
    if (pending.length === 0) {
      await sendMessage(BASE, ADMIN_ID, '📭 خبری در انتظار تایید نیست');
      return;
    }
    await sendMessage(BASE, ADMIN_ID, `📋 <b>${pending.length} خبر در انتظار تایید:</b>`);
    for (const item of pending.slice(0, 5)) {
      await sendReview(BASE, ADMIN_ID, item);
    }
    if (pending.length > 5) {
      await sendMessage(BASE, ADMIN_ID, `📋 ${pending.length - 5} خبر دیگر هم در صف است.`,
        { inline_keyboard: [[{ text: '🗑 پاک کردن کل صف', callback_data: 'clearq' }]] });
    }
  } else if (text === '/clear') {
    await askClearQueue(env, BASE, ADMIN_ID);
  } else if (text === '/runs') {
    await sendLong(BASE, ADMIN_ID, runsReport(await getJSON(env, 'run_log', []), env));
  } else if (/^\/ask\b/i.test(text)) {
    await handleAsk(text, env, BASE, ADMIN_ID, CHANNEL_ID, true);
  } else if (/^\/(quiz|me|leaderboard|hide)\b/i.test(text)) {
    await handleQuizCommand(text, env, BASE, ADMIN_ID, msg.from || { id: ADMIN_ID, first_name: 'ادمین' });
  } else if (text === '/quizboard') {
    const off = (Number(env.DISPLAY_TZ_OFFSET_MIN) || 210) * 60000;
    await sendLong(BASE, ADMIN_ID, leaderboardText(normScores(await getJSON(env, 'quiz_scores', {})), quizWeekId(Date.now(), off)),
      { inline_keyboard: [[{ text: '📣 انتشار در کانال', callback_data: 'qzpost' }]] });
  } else if (text === '/dashboard') {
    await sendLong(BASE, ADMIN_ID, dashboardReport({
      runs: await getJSON(env, 'run_log', []),
      published: await getJSON(env, 'published', []),
      heartbeat: await getJSON(env, 'heartbeat', {}),
      edu: await getJSON(env, 'edu_state', {}),
      budget: await getJSON(env, 'gemini_budget', {}),
      pending: await getJSON(env, 'pending', []),
      approved: await getJSON(env, 'approved', []),
    }, env));
  } else if (text === '/feeds') {
    await sendLong(BASE, ADMIN_ID, feedsReport(await getJSON(env, 'feed_health', {})));
  } else if (text === '/sources') {
    await sendLong(BASE, ADMIN_ID, sourcesReport(await getJSON(env, 'source_stats', {}), env));
  } else if (text === '/pause') {
    await updateState(env, { paused: true });
    await sendMessage(BASE, ADMIN_ID, '⏸️ انتشار متوقف شد');
  } else if (text === '/resume') {
    await updateState(env, { paused: false });
    await sendMessage(BASE, ADMIN_ID, '▶️ انتشار ادامه یافت');
  } else if (/^\/auto/i.test(text) || /^auto[\s_-]*(on|off)$/i.test(text)) {
    const val = parseAutoChoice(text);
    if (val === null) {
      // بدون آرگومان (مثلاً دکمهٔ منوی فرمان‌های تلگرام): وضعیت + دکمه‌های شیشه‌ای
      const current = state.auto_publish_enabled !== false;
      await sendMessage(BASE, ADMIN_ID,
        `انتشار خودکار الان: ${current ? 'روشن ✅' : 'خاموش ❌'}\nیکی را انتخاب کن:`,
        { inline_keyboard: [[
          { text: '🤖 روشن', callback_data: 'auto_on' },
          { text: '⏸ خاموش', callback_data: 'auto_off' }
        ]] }
      );
    } else {
      await setAutoPublish(env, BASE, ADMIN_ID, null, val);
    }
  } else if (/^\/undo/i.test(text)) {
    const { item } = await lastPublishedItem(env);
    if (!item) {
      await sendMessage(BASE, ADMIN_ID, '🗑 پستی برای حذف پیدا نشد (یا قدیمی‌تر از ۴۸ ساعت است).');
    } else {
      await sendMessage(BASE, ADMIN_ID,
        `🗑 <b>حذف آخرین پست؟</b>\n\n«${escapeHtml(item.title_fa || item.title || '?')}»\n\nاین پست از کانال حذف می‌شود.`,
        { inline_keyboard: [[
          { text: '🗑 بله، حذف کن', callback_data: 'undo_yes' },
          { text: '↩️ انصراف', callback_data: 'undo_no' },
        ]] });
    }
  } else if (/^\/editlast/i.test(text)) {
    const { item } = await lastPublishedItem(env);
    if (!item) {
      await sendMessage(BASE, ADMIN_ID, '✏️ پستی برای ویرایش پیدا نشد (یا قدیمی‌تر از ۴۸ ساعت است).');
    } else {
      await updateState(env, { _editing_pub: item.message_id });
      await sendMessage(BASE, ADMIN_ID,
        `✏️ <b>ویرایش آخرین پست</b>\n\n«${escapeHtml(item.title_fa || item.title || '?')}»\n\n` +
        'متن خلاصهٔ جدید را بفرست. (قالب، عنوان و منبع حفظ می‌شوند.)');
    }
  } else if (/^\/promo\b/i.test(text)) {
    // 📣 تبلیغ دوره‌ای سایت/ربات در کانال: /promo (وضعیت) | on | off | now (در اجرای بعدی pipeline)
    const arg = (text.split(/\s+/)[1] || '').toLowerCase();
    if (arg === 'on' || arg === 'off') {
      await updateState(env, { promo_enabled: arg === 'on' });
      await sendMessage(BASE, ADMIN_ID, `📣 تبلیغ دوره‌ای سایت: ${arg === 'on' ? 'روشن ✅' : 'خاموش ❌'}`);
    } else if (arg === 'now') {
      await updateState(env, { promo_force: true });
      await sendMessage(BASE, ADMIN_ID, '📣 در اجرای بعدی ربات (حداکثر حدود یک ساعت) پست تبلیغ سایت در کانال منتشر می‌شود.\n' +
        'اگر زودتر می‌خواهی: Actions → Run pipeline.');
    } else {
      const ps = await getJSON(env, 'promo_state', {});
      const every = Math.max(1, Number(env.PROMO_EVERY_DAYS) || 3);
      const on = state.promo_enabled !== false;
      await sendMessage(BASE, ADMIN_ID,
        `📣 <b>تبلیغ دوره‌ای سایت</b>: ${on ? 'روشن ✅' : 'خاموش ❌'}\n` +
        `🔁 هر ${every} روز یک‌بار، حدود ساعت ${Number(env.PROMO_HOUR) || 18}:۰۰ تهران\n` +
        `📤 تعداد ارسال تاکنون: ${Number(ps.count) || 0}\n` +
        `🕐 آخرین ارسال: ${ps.last_at ? agoText(ps.last_at) : 'هنوز نرفته'}\n` +
        (state.promo_force ? '⏳ ارسال فوری در اجرای بعدی در صف است.\n' : '') +
        '\nتغییر: <code>/promo on</code> · <code>/promo off</code> · <code>/promo now</code>\n' +
        '<i>برای فعال‌بودن، متغیر SITE_URL باید در GitHub (Secrets یا Variables) تنظیم باشد.</i>');
    }
  } else if (/^\/edu/i.test(text)) {
    // پست آموزشی (Gemini + OpenRouter رایگان، بازهٔ طولانی): /edu on | /edu off | /edu (وضعیت)
    const arg = (text.split(/\s+/)[1] || '').toLowerCase();
    if (arg === 'on' || arg === 'off') {
      await updateState(env, { edu_enabled: arg === 'on' });
      await sendMessage(BASE, ADMIN_ID, `🎓 پست آموزشی: ${arg === 'on' ? 'روشن ✅' : 'خاموش ❌'}`);
    } else {
      const est = await getJSON(env, 'edu_state', {});
      const published = await getJSON(env, 'published', []);
      const on = state.edu_enabled !== false;
      const recent = (est.recent || []).slice(-5).reverse().map(r => `• ${r.title}`).join('\n');
      // آمار مصرف مدل‌ها: امروز و دیروز (به وقت ایران)
      const dayKey = (offset) => new Date(Date.now() + 210 * 60000 - offset * 86400000).toISOString().slice(0, 10);
      // پیشرفت شب: چند پست/کوییز از سقف روزانه منتشر شده و چند تا مانده
      const target = Number(env.EDU_POSTS_PER_DAY) || 4;
      const quizTarget = Number(env.EDU_QUIZ_PER_DAY) || 2;
      const doneToday = Number((est.pub_count || {})[dayKey(0)] || 0);
      const quizToday = Number((est.quiz_count || {})[dayKey(0)] || 0);
      const quizLeft = Math.max(0, quizTarget - quizToday);
      const queueN = (est.queue || []).length;
      const eduTotal = published.filter(p => p && p.published && p.source === 'آموزش AI').length;
      const usageLine = (label, u) => !u ? '' :
        `${label}: ساخت ${u.ok || 0} از ${u.attempts || 0} تلاش | درخواست به مدل‌ها ${u.calls || 0} | خروجی ردشده (توکن هدررفته) ${u.rejected || 0}` +
        (u.via && Object.keys(u.via).length ? `\n   ${Object.entries(u.via).map(([k, v]) => `${k} ×${v}`).join('، ')}` : '');
      const usage = [usageLine('امروز', (est.usage || {})[dayKey(0)]), usageLine('دیروز', (est.usage || {})[dayKey(1)])]
        .filter(Boolean).join('\n');
      await sendMessage(BASE, ADMIN_ID,
        `🎓 پست آموزشی: ${on ? 'روشن ✅' : 'خاموش ❌'} | کوییز: ${quizTarget > 0 ? 'روشن ✅' : 'خاموش ❌'}\n` +
        `📤 امشب منتشر شد: ${doneToday}/${target} پست · ${quizToday}/${quizTarget} کوییز\n` +
        `📥 در صف انتشار: ${queueN} پست · ${quizLeft} کوییز باقی‌ماندهٔ امشب\n` +
        `🗂 کل پست‌های آموزشی منتشرشده: ${eduTotal}\n` +
        (usage ? `\n📊 مصرف:\n${escapeHtml(usage)}\n\n` : '') +
        'تغییر: <code>/edu on</code> یا <code>/edu off</code>' +
        (recent ? `\n\nآخرین پست‌ها:\n${escapeHtml(recent)}` : ''));
    }
  } else if (/^\/welcome/i.test(text)) {
    // 👋 پست خوش‌آمد (بر اساس تازه‌ترین خبرهای منتشرشده) + سنجاق در کانال
    const published = await getJSON(env, 'published', []);
    const recent = published
      .filter(p => p && p.published && !p.deleted && p.link)
      .slice(-3)
      .reverse();
    let msg = '👋 <b>به کانال «اخبار هوش مصنوعی» خوش آمدی</b>\n\n';
    msg += 'اینجا مهم‌ترین خبرهای دنیای AI را — ترجمه‌شده، خلاصه و با تیتر فارسی — می‌بینی؛ ' +
      'به‌همراه آموزش‌های کوتاه و کاربردی.\n\n';
    msg += '📌 <b>چه چیزی دریافت می‌کنی؟</b>\n';
    msg += '• خبرهای لحظه‌ای از ۳۷ منبع معتبر (OpenAI، Google، Anthropic، Meta، Mistral، Qwen و رسانه‌های بزرگ)\n';
    msg += '• خلاصهٔ فارسی + متن اصلی؛ خبرهای مهم با تایید انسانی منتشر می‌شوند\n';
    msg += '• هر شب ساعت ۱۹ تا ۲۲: ۴ پست آموزشی + ۲ کوییز تعاملی\n';
    msg += '• هر جمعه: جمع‌بندی هفته با ۷ خبر مهم و پاسخ کوییزها\n';
    msg += '• هر روز: یک مقالهٔ تازه از arXiv\n\n';
    msg += '🗂 <b>راهنمای سریع</b>\n';
    msg += '• آرشیو موضوعی: دکمهٔ «🗂 آرشیو موضوعی» در منوی /start\n';
    msg += '• خبرهای مهم 📌 سنجاق می‌شوند\n';
    msg += '• هشتگ‌های پرکاربرد: #مدل_جدید #ایمنی_AI #متن_باز #ایجنت\n';
    if (env.CHANNEL_GROUP_URL) msg += `• گروه بحث: ${env.CHANNEL_GROUP_URL}\n`;
    msg += '\n📣 اگر برایت مفید بود، کانال را برای دوستت فوروارد کن.';
    if (recent.length) {
      msg += '<b>تازه‌ترین خبرها:</b>\n';
      recent.forEach((p, i) => {
        const link = safeLink(p.link);
        const title = escapeHtml(clip(p.title_fa || p.title || '', 60));
        msg += `${i + 1}. ${link ? `<a href="${link}">${title}</a>` : title}\n`;
      });
      msg += '\n';
    }
    msg += 'دستورها: /pending · /status · /edu · /health';
    const res = await sendMessage(BASE, CHANNEL_ID, msg);
    const mid = res && res.result && res.result.message_id;
    if (mid) {
      await telegramCall(BASE, 'pinChatMessage', {
        chat_id: CHANNEL_ID, message_id: mid, disable_notification: false,
      });
    }
    try { await sendBaleText(env, msg.replace(/<[^>]+>/g, '')); } catch (e) { /* نادیده */ }
    await sendMessage(BASE, ADMIN_ID, mid ? '✅ پست خوش‌آمد در کانال سنجاق شد' : '⚠️ ارسال نشد');
  } else if (/^\/topics/i.test(text)) {
    // 🎯 موضوعات مورد علاقهٔ ادمین: اولویت‌دهی، نه فیلتر
    const topics = Array.isArray(state.topics) ? state.topics : [];
    const rest = text.replace(/^\/topics\s*/i, '').trim();
    const [cmd, ...words] = rest.split(/\s+/);
    const value = words.join(' ').trim().slice(0, 40);
    if (!rest) {
      const lines = topics.length
        ? topics.map((t, i) => `${i + 1}. ${escapeHtml(t)}`).join('\n')
        : 'هنوز موضوعی ثبت نشده.';
      await sendMessage(BASE, ADMIN_ID,
        `🎯 <b>موضوعات مورد علاقه</b>\n${lines}\n\n` +
        'افزودن: <code>/topics add ایمنی</code>\n' +
        'حذف: <code>/topics del ایمنی</code>\n' +
        'پاک‌کردن همه: <code>/topics clear</code>\n\n' +
        '<i>این فقط اولویت است؛ بقیهٔ خبر‌ها هم منتشر می‌شوند.</i>');
    } else if (/^clear$/i.test(cmd)) {
      await updateState(env, { topics: [] });
      await sendMessage(BASE, ADMIN_ID, '🗑 فهرست موضوعات پاک شد.');
    } else if (/^(add|\+)$/i.test(cmd) && value) {
      if (topics.length >= 10) {
        await sendMessage(BASE, ADMIN_ID, '⚠️ حداکثر ۱۰ موضوع؛ اول یکی را حذف کن.');
      } else {
        if (!topics.some(t => String(t).toLowerCase() === value.toLowerCase())) topics.push(value);
        await updateState(env, { topics });
        await sendMessage(BASE, ADMIN_ID,
          `✅ موضوع ثبت شد: <b>${escapeHtml(value)}</b>\n🎯 ${topics.map(t => escapeHtml(t)).join(' · ')}`);
      }
    } else if (/^(del|remove|-)$/i.test(cmd) && value) {
      const next = topics.filter(t => String(t).toLowerCase() !== value.toLowerCase());
      await updateState(env, { topics: next });
      await sendMessage(BASE, ADMIN_ID, next.length === topics.length
        ? `⚠️ پیدا نشد: ${escapeHtml(value)}`
        : `🗑 حذف شد: <b>${escapeHtml(value)}</b>`);
    } else {
      await sendMessage(BASE, ADMIN_ID, 'فرمت: <code>/topics add ایمنی</code>');
    }
  } else if (/^\/health/i.test(text)) {
    // همان اطلاعات GET /health، این‌بار داخل تلگرام
    const h = await healthSnapshot(env);
    const ago = h.lastHeartbeat ? Math.round((Date.now() - Date.parse(h.lastHeartbeat)) / 60000) : null;
    const last = h.lastHeartbeat
      ? `${h.lastHeartbeat}${ago !== null ? ` (${ago} دقیقه پیش)` : ''}`
      : 'ثبت نشده — یعنی هنوز اجرایی با کد جدید تمام نشده';
    const target = Number(env.EDU_POSTS_PER_DAY) || 4;
    await sendMessage(BASE, ADMIN_ID,
      `🩺 <b>سلامت ربات</b>\n\n` +
      `KV: ${h.kv ? '✅ سالم' : '❌ خطا'}\n` +
      `مرحله: ${escapeHtml(h.stage)}${h.paused ? ' (متوقف)' : ''}\n` +
      `آخرین اجرا: ${escapeHtml(last)}\n` +
      `صف: ${h.queue.pending} در انتظار · ${h.queue.approved} آماده انتشار\n` +
      `منتشرشده (کل): ${h.published}\n` +
      `آموزشی امروز: ${h.edu.today}/${target} (صف: ${h.edu.queue})`);
  } else if (text === '/top') {
    const published = await getJSON(env, 'published', []);
    const reactions = await getJSON(env, 'reactions', {});
    const rxWeight = Math.max(0, Number(env.REACTIONS_WEIGHT) || 10);
    const rxOn = String(env.REACTIONS_ENABLED ?? 'true').toLowerCase() !== 'false';
    const rows = published
      .filter(p => p && p.published && !p.deleted)
      .map(p => {
        const rx = (rxOn && reactions[String(p.message_id)]) || {};
        const by = (rx && rx.by) || {};
        const topEmoji = Object.keys(by).sort((a, b) => Number(by[b]) - Number(by[a]))[0] || '';
        const reactionTotal = Number(rx.total) || 0;
        const views = Number(p.views) || 0;
        return { item: p, views, reactionTotal, topEmoji,
          engagement: views + (rxOn ? reactionTotal * rxWeight : 0) };
      })
      .filter(r => r.engagement > 0)
      .sort((a, b) => b.engagement - a.engagement)
      .slice(0, 10);
    if (!rows.length) {
      const hint = env.CHANNEL_HANDLE
        ? ''
        : '\n\n⚠️ برای فعال‌شدن، متغیر <code>CHANNEL_HANDLE</code> را مثل <code>@your_channel</code> تنظیم کن.';
      await sendMessage(BASE, ADMIN_ID, '📈 هنوز بازدید/واکنشی ثبت نشده است.' + hint);
    } else {
      const lines = rows.map((r, i) => {
        const p = r.item;
        const stats = [];
        if (r.views > 0) stats.push(`👁 ${fmtViews(r.views)}`);
        if (r.reactionTotal > 0) stats.push(`${r.topEmoji || '❤️'} ${fmtViews(r.reactionTotal)}`);
        return `${i + 1}. <b>${stats.join(' · ')}</b> — ${escapeHtml(String(p.title_fa || p.title || '').slice(0, 70))}` +
          (p.source ? `\n   <i>${escapeHtml(String(p.source).slice(0, 40))}</i>` : '');
      }).join('\n');
      await sendMessage(BASE, ADMIN_ID,
        `📈 <b>پربازدیدترین‌های اخیر</b>\n${'─'.repeat(20)}\n${lines}\n\n<i>نمونه: ${rows.length} پست</i>`);
    }
  } else if (text === '/weekly') {
    const ws = await getJSON(env, 'weekly_state', {});
    const lines = ['🗓 <b>جمع‌بندی هفتگی</b>'];
    if (ws.last_date) lines.push(`آخرین اجرا: ${escapeHtml(ws.last_date)}`);
    if (ws.poll) {
      if (ws.poll.closed) {
        lines.push(`نظرسنجی: بسته ✅ (کل رأی: ${ws.poll.total_votes || 0})`);
      } else {
        lines.push(`نظرسنجی: باز — بسته‌شدن: ${escapeHtml(String(ws.poll.closes_at || '').slice(0, 16))}`);
      }
    } else {
      lines.push('نظرسنجی: هنوز شروع نشده');
    }
    const result = ws.result || {};
    if (result.category && (!result.expires_at || Date.parse(result.expires_at) > Date.now())) {
      lines.push(`🏆 انتخاب مخاطبان: <b>${escapeHtml(result.label || result.category)}</b> ` +
        `(${result.votes || 0}/${result.total || 0} رأی)`);
      lines.push(`⏳ اثر بوست تا: ${escapeHtml(String(result.expires_at || '').slice(0, 10))}`);
    }
    await sendMessage(BASE, ADMIN_ID, lines.join('\n'));
  } else if (text === '/tips') {
    const tips = await getJSON(env, 'tips', []);
    const rows = tips.filter(t => t && t.status === 'new').slice(-5);
    if (!rows.length) {
      await sendMessage(BASE, ADMIN_ID, '📭 سوژهٔ تازه‌ای از مخاطبان نیست.');
    }
    for (const tip of rows) {
      await sendMessage(BASE, ADMIN_ID,
        `📣 <b>سوژهٔ تازه</b>\nاز: ${escapeHtml(String(tip.user || tip.chat_id || '?'))}\n\n` +
        `${escapeHtml(String(tip.text || '').slice(0, 600))}`,
        { inline_keyboard: [[
          { text: '✅ انتشار', callback_data: `tipok:${tip.id}` },
          { text: '❌ رد', callback_data: `tipno:${tip.id}` },
        ]] });
    }
  } else if (text === '/backup') {
    await sendBackup(env, BASE, ADMIN_ID);
  } else if (text === '/stats') {
    const published = await getJSON(env, 'published', []);
    const totalPublished = published.filter(p => p.published).length;
    const totalRejected = published.filter(p => !p.published).length;

    // آمار بر اساس منبع
    const sourceCounts = {};
    for (const p of published) {
      if (p.published) {
        sourceCounts[p.source] = (sourceCounts[p.source] || 0) + 1;
      }
    }

    // آمار بر اساس دسته‌بندی
    const categoryCounts = {};
    for (const p of published) {
      if (p.published) {
        categoryCounts[p.category] = (categoryCounts[p.category] || 0) + 1;
      }
    }

    // آمار بر اساس اعتبار
    const confidenceCounts = { confirmed: 0, likely: 0, rumor: 0 };
    for (const p of published) {
      if (p.published && confidenceCounts[p.confidence] !== undefined) {
        confidenceCounts[p.confidence]++;
      }
    }

    // آمار بر اساس اهمیت
    const importanceCounts = { high: 0, medium: 0, low: 0 };
    for (const p of published) {
      if (p.published && importanceCounts[p.importance] !== undefined) {
        importanceCounts[p.importance]++;
      }
    }

    const sourceLines = Object.entries(sourceCounts)
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => `  ${name}: ${count}`)
      .join('\n');

    const categoryLines = Object.entries(categoryCounts)
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => `  ${name}: ${count}`)
      .join('\n');

    await sendMessage(BASE, ADMIN_ID,
      `📊 <b>آمار کامل سیستم</b>\n\n` +
      `━━━━━━━━━━━━━━\n` +
      `📈 کل منتشر شده: <b>${totalPublished}</b>\n` +
      `❌ کل رد شده: <b>${totalRejected}</b>\n` +
      `📋 در انتظار: <b>${pending.length}</b>\n` +
      `✅ آماده انتشار: <b>${approved.length}</b>\n\n` +
      `━━━━━━━━━━━━━━\n` +
      `📰 بر اساس منبع:\n${sourceLines}\n\n` +
      `━━━━━━━━━━━━━━\n` +
      `📂 بر اساس دسته‌بندی:\n${categoryLines}\n\n` +
      `━━━━━━━━━━━━━━\n` +
      `🔒 بر اساس اعتبار:\n` +
      `  قطعی: ${confidenceCounts.confirmed}\n` +
      `  احتمالی: ${confidenceCounts.likely}\n` +
      `  شایعه: ${confidenceCounts.rumor}\n\n` +
      `━━━━━━━━━━━━━━\n` +
      `🔥 بر اساس اهمیت:\n` +
      `  بالا: ${importanceCounts.high}\n` +
      `  متوسط: ${importanceCounts.medium}\n` +
      `  پایین: ${importanceCounts.low}`
    );
  }
}

async function sendReview(BASE, ADMIN_ID, item) {
  const trust = Number(item.source_trust) || 3;
  const trustEmoji = trust >= 4 ? '🟢' : trust >= 3 ? '🟡' : '🔴';
  const score = item.score || 0;
  const scoreLabel = score >= 70 ? 'زیاد' : score >= 50 ? 'متوسط' : score >= 30 ? 'کم' : 'ضعیف';
  const scoreEmoji = score >= 70 ? '🟢' : score >= 50 ? '🟡' : score >= 30 ? '🟠' : '🔴';

  const id = itemKey(item);
  let text = `📰 <b>${escapeHtml(item.title_fa)}</b>\n\n`;
  text += `${escapeHtml(item.summary_fa)}\n\n`;
  if (item.translated === false) {
    text += `🔤 <i>ترجمه نشده — متن اصلی</i>\n\n`;
  } else if (item.title && item.lang && item.lang !== 'fa') {
    text += `🌐 ${escapeHtml(clip(item.title, 150))}\n\n`;
  }
  text += `🆔 ID: <code>${escapeHtml(id || 'N/A')}</code>\n`;
  text += `📌 اهمیت: ${escapeHtml(item.importance)} | اعتبار: ${escapeHtml(item.confidence)}\n`;
  text += `${trustEmoji} منبع: ${escapeHtml(item.source)} (${trust.toFixed(1)}/5)`;
  if (item.corroborated_by) text += ` — 🔗 تایید شده توسط ${item.corroborated_by} منبع دیگر`;
  text += `\n${scoreEmoji} اسکور: ${score}/100 (${scoreLabel})\n`;
  if (item.hot !== undefined) {
    const why = (item.hot_reasons || []).join('، ');
    text += `🔥 جذابیت: ${item.hot}/100 (حد انتشار خودکار: ${Math.round(item.hot_threshold || 0)})`;
    text += (why ? ` — ${escapeHtml(why)}` : '') + '\n';
  }
  text += `🏷️ ${(item.hashtags || []).map(h => '#' + escapeHtml(h)).join(' ')}\n`;
  text += `🔗 ${escapeHtml(item.link)}`;
  if (item.admin_question) {
    text += `\n\n❓ ${escapeHtml(item.admin_question)}`;
  }

  const keyboard = {
    inline_keyboard: [
      [
        { text: '✅ تایید', callback_data: `approve:${id}` },
        { text: '❌ رد', callback_data: `reject:${id}` }
      ],
      [
        { text: '⭐️ ویژه', callback_data: `feature:${id}` },
        { text: '✏️ ویرایش', callback_data: `edit:${id}` }
      ],
      [
        { text: '⏰ +۲ ساعت', callback_data: `sched:2:${id}` },
        { text: '⏰ +۶ ساعت', callback_data: `sched:6:${id}` }
      ],
      [
        { text: '📌 تایید و سنجاق', callback_data: `pin:${id}` }
      ]
    ]
  };

  // دکمه لینک فقط با لینک معتبر (وگرنه تلگرام کل پیام را رد می‌کند)
  if (typeof item.link === 'string' && item.link.startsWith('http')) {
    keyboard.inline_keyboard.push([{ text: '🔗 مشاهده منبع', url: item.link }]);
  }
  keyboard.inline_keyboard.push([{ text: '🗑 پاک کردن کل صف', callback_data: 'clearq' }]);

  await sendMessage(BASE, ADMIN_ID, text, keyboard);
}

// ───────────── پاک‌کردن صف ─────────────

async function askClearQueue(env, BASE, ADMIN_ID) {
  const pending = await getJSON(env, 'pending', []);
  const approved = await getJSON(env, 'approved', []);
  if (!pending.length && !approved.length) {
    await sendMessage(BASE, ADMIN_ID, '📭 صف خالی است');
    return;
  }
  const rows = [[{ text: `🗑 فقط صف تایید (${pending.length})`, callback_data: 'clrp' }]];
  if (approved.length) {
    rows.push([{ text: `🗑 همه، با صف آماده انتشار (${pending.length + approved.length})`, callback_data: 'clra' }]);
  }
  rows.push([{ text: '↩️ انصراف', callback_data: 'clrx' }]);
  await sendMessage(BASE, ADMIN_ID,
    `⚠️ <b>پاک‌کردن صف</b>\n\n📋 در انتظار تایید: ${pending.length}\n✅ آماده انتشار: ${approved.length}\n\n` +
    'این کار برگشت‌پذیر نیست. (ردشدن‌ها در اعتبار منابع حساب نمی‌شوند.)',
    { inline_keyboard: rows });
}

// ───────────── اعتبار پویای منابع ─────────────
// معادل src/source_trust.py — اگر آنجا را تغییر دادی، اینجا را هم عوض کن.

const TRUST_PRIOR_WEIGHT_DEFAULT = 8;
const TRUST_HALF_LIFE_DEFAULT = 30;
const TRUST_FEATURE_WEIGHT = 1.5;
const AUTO_TRUST_THRESHOLD = 4.0;

function decayFactor(updated, now, env) {
  const last = updated ? Date.parse(updated) : NaN;
  if (!Number.isFinite(last) || now <= last) return 1;
  const days = (now - last) / 86400000;
  const half = Number(env?.TRUST_HALF_LIFE_DAYS) || TRUST_HALF_LIFE_DEFAULT;
  return Math.pow(0.5, days / half);
}

function computeTrust(prior, pos, neg, env) {
  const k = Number(env?.TRUST_PRIOR_WEIGHT) || TRUST_PRIOR_WEIGHT_DEFAULT;
  const priorRate = (Math.max(1, Math.min(5, prior)) - 1) / 4;
  const rate = (pos + priorRate * k) / (pos + neg + k);
  return Math.round((1 + 4 * rate) * 100) / 100;
}

function trustOf(entry, env, now = Date.now()) {
  const f = decayFactor(entry.updated, now, env);
  return computeTrust(Number(entry.prior) || 3, (entry.pos || 0) * f, (entry.neg || 0) * f, env);
}

/** ثبت تصمیم ادمین: action = approve | feature | reject */
async function recordDecision(env, item, action, now = Date.now()) {
  try {
    if (!item || !item.source || !['approve', 'feature', 'reject'].includes(action)) return;
    const stats = await getJSON(env, 'source_stats', {});
    const entry = stats[item.source] || { prior: Number(item.source_prior) || 3, pos: 0, neg: 0 };
    const f = decayFactor(entry.updated, now, env);
    let pos = (entry.pos || 0) * f;
    let neg = (entry.neg || 0) * f;
    if (action === 'reject') neg += 1;
    else pos += action === 'feature' ? TRUST_FEATURE_WEIGHT : 1;
    entry.pos = Math.round(pos * 1000) / 1000;
    entry.neg = Math.round(neg * 1000) / 1000;
    entry.updated = new Date(now).toISOString();
    stats[item.source] = entry;
    await saveJSON(env, 'source_stats', stats);
  } catch (e) {
    console.error('recordDecision failed:', e);
  }
}

function agoText(iso, now = Date.now()) {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '؟';
  const mins = Math.max(0, Math.floor((now - t) / 60000));
  if (mins < 60) return `${mins} دقیقه پیش`;
  if (mins < 60 * 48) return `${Math.floor(mins / 60)} ساعت پیش`;
  return `${Math.floor(mins / 1440)} روز پیش`;
}

function runsReport(log, env, n = 12) {
  if (!Array.isArray(log) || !log.length) return '📈 هنوز اجرایی ثبت نشده (بعد از اولین اجرای pipeline پر می‌شود).';
  const off = (Number(env?.DISPLAY_TZ_OFFSET_MIN) || 210) * 60000;
  const lines = ['📈 <b>اجراهای اخیر</b> (ساعت محلی)\n'];
  for (const r of log.slice(-n).reverse()) {
    const t = Date.parse(r.t);
    const d = Number.isFinite(t) ? new Date(t + off).toISOString() : null;
    const hhmm = d ? `${d.slice(5, 10)} ${d.slice(11, 16)}` : '؟';
    const tag = r.forced ? '🖐' : '⏱';
    if (r.paused && !r.collected) lines.push(`${tag} ${hhmm} — ⏸ ربات متوقف است`);
    else if (r.skipped) lines.push(`${tag} ${hhmm} — جمع‌آوری نشد (فاصله از اجرای قبلی کم بود) | منتشر: ${r.published || 0}`);
    else if (r.collected) {
      lines.push(`${tag} ${hhmm} — فید ${r.feeds_ok ?? '?'}/${r.feeds_total ?? '?'} | جدید ${r.fetched || 0} → تحلیل ${r.analyzed || 0} ` +
        `(🤖${r.auto || 0} 👀${r.pending || 0} ❌${(r.low_score || 0) + (r.rejected_keyword || 0)}) | ` +
        `📤 منتشر ${r.published || 0} | صف: ${r.queue_pending || 0} تایید، ${r.queue_approved || 0} آماده` +
        (r.untranslated ? ` | 🔤 ${r.untranslated} ترجمه‌نشده` : '') +
        (r.kv_put ? ` | 💾 ${r.kv_put} نوشتن` : '') +
        (Array.isArray(r.mod_fail) && r.mod_fail.length ? ` | ⚠️ ${r.mod_fail.join('،')}` : ''));
    } else lines.push(`${tag} ${hhmm} — جمع‌آوری انجام نشد | منتشر: ${r.published || 0}`);
  }
  lines.push('\n🤖 خودکار | 👀 نیازمند تایید شما | ❌ ردشده (کم‌امتیاز/کلمه مشکوک)');
  return lines.join('\n');
}

/**
 * 📟 داشبورد ادمین: خلاصهٔ ۲۴ ساعت اخیر در یک پیام.
 * فقط از دادهٔ موجود در KV می‌خواند (هیچ نوشتنی ندارد).
 */
function dashboardReport(d, env, now = Date.now()) {
  const dayAgo = now - 24 * 3600000;
  const runs = (Array.isArray(d.runs) ? d.runs : []).filter(r => Date.parse(r.t) >= dayAgo);
  const sum = (k) => runs.reduce((a, r) => a + (Number(r[k]) || 0), 0);
  const pub = (Array.isArray(d.published) ? d.published : []).filter(p => p && Date.parse(p.published_at || p.rejected_at || '') >= dayAgo);
  const posted = pub.filter(p => p.published);
  const rejected = pub.filter(p => !p.published);
  const lines = ['📟 <b>داشبورد ۲۴ ساعت اخیر</b>\n'];

  lines.push(`🏃 اجراها: ${runs.length} | جمع‌آوری‌شده: ${sum('fetched')} | تحلیل: ${sum('analyzed')}`);
  lines.push(`📤 منتشرشده: ${posted.length} | ❌ ردشده: ${rejected.length} | 🤖 خودکار: ${sum('auto')} | 👀 نیازمند تایید: ${sum('pending')}`);
  const decided = sum('auto') + sum('pending');
  if (decided) lines.push(`🎯 نرخ انتشار خودکار: ${Math.round(100 * sum('auto') / decided)}٪`);
  lines.push(`📋 صف: ${(d.pending || []).length} تایید | ${(d.approved || []).length} آماده`);

  // دسته‌های منتشرشده
  const cats = {};
  for (const p of posted) cats[p.category || '؟'] = (cats[p.category || '؟'] || 0) + 1;
  const topCats = Object.entries(cats).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([c, n]) => `${c} ${n}`);
  if (topCats.length) lines.push(`🏷 دسته‌ها: ${escapeHtml(topCats.join(' | '))}`);

  // ساعات اوج انتشار (به وقت نمایش)
  const off = (Number(env?.DISPLAY_TZ_OFFSET_MIN) || 210) * 60000;
  const hours = {};
  for (const p of posted) {
    const t = Date.parse(p.published_at || '');
    if (Number.isFinite(t)) { const h = new Date(t + off).getUTCHours(); hours[h] = (hours[h] || 0) + 1; }
  }
  const peak = Object.entries(hours).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([h, n]) => `${String(h).padStart(2, '0')}:00 (${n})`);
  if (peak.length) lines.push(`🕒 ساعت‌های پرانتشار: ${peak.join(' | ')}`);

  // علت ردها (برای تنظیم فیلتر)
  const why = {};
  for (const p of rejected) why[p.reject_reason || 'admin'] = (why[p.reject_reason || 'admin'] || 0) + 1;
  const whyText = Object.entries(why).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${k} ${n}`);
  if (whyText.length) lines.push(`🚫 علت ردها: ${escapeHtml(whyText.join(' | '))}`);

  // KV
  const kvPuts = sum('kv_put');
  if (runs.length) lines.push(`💾 نوشتن KV توسط pipeline: ${kvPuts} (میانگین ${Math.round(kvPuts / runs.length)} در هر اجرا؛ سقف رایگان روزانه ≈ ۱۰۰۰ برای کل حساب)`);

  // مدل‌ها
  const edu = d.edu || {};
  const todayKey = new Date(now + off).toISOString().slice(0, 10);
  const u = (edu.usage || {})[todayKey] || {};
  const via = Object.entries(u.via || {}).map(([k, n]) => `${k} ${n}`).join('، ');
  lines.push(`🎓 پست آموزشی امروز: ${Number((edu.pub_count || {})[todayKey] || 0)} منتشر | صف ${(edu.queue || []).length} | تلاش ${u.attempts || 0} (✅${u.ok || 0} ❌${u.fail || 0})` + (via ? ` | ${escapeHtml(via)}` : ''));
  if (d.budget && d.budget.count != null) lines.push(`🤖 درخواست Gemini امروز: ${d.budget.count}`);

  // ماژول‌ها
  const mods = Object.entries((d.heartbeat || {}).modules || {});
  const badMods = mods.filter(([, m]) => m && m.ok === false);
  lines.push('');
  if (!mods.length) lines.push('🧩 ماژول‌ها: هنوز گزارشی نیست (بعد از اجرای بعدی pipeline).');
  else if (!badMods.length) lines.push(`🧩 ماژول‌ها: همه سالم (${mods.length})`);
  else {
    lines.push(`🧩 ماژول‌های دارای خطا (${badMods.length}/${mods.length}):`);
    for (const [n, m] of badMods) lines.push(`   ⚠️ <b>${escapeHtml(n)}</b> — ${escapeHtml(clip(m.err || '?', 80))} (پیاپی: ${m.fails || 0})`);
  }
  if ((d.heartbeat || {}).at) lines.push(`🕐 آخرین اجرا: ${agoText(d.heartbeat.at, now)}`);
  return lines.join('\n');
}

function feedsReport(health) {
  const entries = Object.entries(health || {});
  if (!entries.length) return '📡 هنوز گزارشی نیست (بعد از اولین جمع‌آوری پر می‌شود).';
  const bad = entries.filter(([, h]) => !h.ok).sort();
  const good = entries.filter(([, h]) => h.ok).sort();
  const lines = [`📡 <b>سلامت فیدها</b>  ✅${good.length}  ❌${bad.length}\n`];
  for (const [n, h] of bad) {
    lines.push(`❌ <b>${escapeHtml(n)}</b> — ${escapeHtml(h.err || '?')} (پیاپی: ${h.fails || 0})`);
  }
  let samples = 0;  // نمونهٔ ردشده‌ها در کل گزارش حداکثر ۱۰ تا (قبلاً تا ۳ تا برای هر فید → گزارش از سقف تلگرام رد می‌شد)
  for (const [n, h] of good) {
    lines.push(`✅ ${escapeHtml(n)}: ${h.items || 0} آیتم، ${h.new || 0} جدید` + (h.filtered ? `، ${h.filtered} ردشده با فیلتر` : '') +
      (h.old ? `، ${h.old} کهنه` : '') + (h.newest ? ` | آخرین خبر فید: ${agoText(h.newest)}` : ''));
    for (const t of (h.filtered_titles || []).slice(0, 2)) {
      if (samples >= 10) break;
      samples++;
      lines.push(`      🚫 ${escapeHtml(clip(t, 70))}`);
    }
  }
  const checked = entries.map(([, h]) => h.checked || '').sort().pop();
  if (checked) lines.push(`\n🕐 آخرین بررسی: ${checked.slice(0, 16).replace('T', ' ')} UTC`);
  return lines.join('\n');
}

function sourcesReport(stats, env) {
  const rows = Object.entries(stats || {}).map(([name, e]) => {
    const base = Number(e.prior) || 3;
    const now = trustOf(e, env);
    const arrow = now - base >= 0.3 ? '🔼' : base - now >= 0.3 ? '🔽' : '▫️';
    const auto = now >= AUTO_TRUST_THRESHOLD ? '🤖' : '👀';
    return { now, line: `${auto}${arrow} ${escapeHtml(name)}: ${base} ← <b>${now.toFixed(1)}</b>  (✅${(e.pos || 0).toFixed(1)} ❌${(e.neg || 0).toFixed(1)})` };
  });
  if (!rows.length) return '📡 هنوز آماری برای منابع ثبت نشده (بعد از اولین اجرای pipeline پر می‌شود).';
  rows.sort((a, b) => b.now - a.now);
  return '📡 <b>اعتبار پویای منابع</b>\n' +
    `🤖 = انتشار خودکار (≥ ${AUTO_TRUST_THRESHOLD}) | 👀 = نیازمند تایید شما\n\n` +
    rows.map(r => r.line).join('\n');
}

// ───────────── قالب پیام کانال (معادل src/formatting.py) ─────────────

function clip(text, n) {
  const t = String(text ?? '').trim();
  return t.length <= n ? t : t.slice(0, n - 1).trimEnd() + '…';
}

/** اعداد فارسی برای نمایش زیباتر در پیام‌های مدیریتی */
function faNum(value) {
  return String(value ?? 0).replace(/\d/g, d => '۰۱۲۳۴۵۶۷۸۹'[Number(d)]);
}

/** فقط لینک http/https معتبر؛ لینک خراب نباید href را بشکند و کل پیام را 400 کند. */
function safeLink(link) {
  const raw = String(link ?? '').trim();
  if (!raw || /[\s"<>]/.test(raw)) return '';
  try {
    const u = new URL(raw);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';
    if (!u.hostname) return '';
    return escapeHtml(raw);
  } catch {
    return '';
  }
}

const WHY_TEXT = {
  model_release: 'یک گزینهٔ تازه برای انتخاب ابزار؛ ارزش واقعی‌اش را با همان کار روزمرهٔ خودت بسنج، نه فقط با تیتر.',
  benchmark: 'بنچمارک فقط یک زاویه از کیفیت را نشان می‌دهد؛ برای تصمیم، مدل را روی کار واقعی خودت امتحان کن.',
  funding: 'جهت رشد بازار در ۶ تا ۱۲ ماه آینده را نشان می‌دهد: ابزار بیشتر، رقابت شدیدتر و احتمال تغییر قیمت‌ها.',
  policy: 'قانون و سیاست تعیین می‌کند چه ابزاری در دسترس باشد و با چه محدودیتی؛ اثرش مستقیم روی انتخاب‌های روزمره است.',
  research: 'پژوهش معمولاً با تأخیر به محصول می‌رسد؛ سیگنال آینده است، نه ابزار قابل استفادهٔ امروز.',
  tool_release: 'یعنی می‌توانی همین امروز یک کار مشخص را سریع‌تر انجام دهی؛ قبل از جایگزینی، خروجی‌اش را با روش فعلی مقایسه کن.',
  industry: 'تغییر در صنعت روی قیمت، دسترسی و پشتیبانی ابزارهایی که استفاده می‌کنی اثر می‌گذارد.',
};
const WHY_RUMOR = 'هنوز رسمی نیست؛ تا تأیید، روی آن برنامه‌ریزی نکن و منتظر منبع اصلی بمان.';

function whyItMatters(item, env) {
  if (String(env?.SHOW_WHY_IT_MATTERS ?? 'true').toLowerCase() === 'false') return '';
  if (!item || ['edu', 'digest', 'paper', 'brief', 'weekly'].includes(item.category)) return '';
  if (item.confidence === 'rumor') return WHY_RUMOR;
  return WHY_TEXT[item.category] || '';
}

/** ساخت پیام با طول‌های مشخص — تنها جایی که HTML کانال تولید می‌شود.
 * برش نهایی هرگز روی HTML انجام نمی‌شود (تگ نصفه = خطای 400 تلگرام).
 */
function renderChannelMessage(item, env, summaryLen, showOriginal, tagCount = -1) {
  let emoji = '🤖';
  let divider = '─'.repeat(20);
  if (item.category === 'fast_track') emoji = '📡';
  else if (item.admin_action === 'featured') { emoji = '⭐️'; divider = '═'.repeat(20); }
  else if (item.importance === 'high') emoji = '🔥';
  else if (item.confidence === 'rumor') emoji = '⚠️';

  const untranslated = item.translated === false;
  const title = escapeHtml(clip(item.title_fa || item.title || '', 300));
  const summary = summaryLen > 0 ? escapeHtml(clip(item.summary_fa, summaryLen)) : '';
  const source = escapeHtml(clip(item.source, 120));
  let tags = item.hashtags || [];
  if (tagCount >= 0) tags = tags.slice(0, tagCount);
  const hashtags = tags.map(h => '#' + escapeHtml(h)).join(' ');

  let original = '';
  if (showOriginal && item.lang && item.lang !== 'fa' && !untranslated && item.title) {
    const oTitle = escapeHtml(clip(item.title, 200));
    const oSummary = escapeHtml(clip(item.summary, 500));
    const body = `<b>${oTitle}</b>` + (oSummary ? `\n\n${oSummary}` : '');
    original = `\n\n🌐 <b>متن اصلی</b>\n<blockquote expandable>${body}</blockquote>`;
  }

  const reason = whyItMatters(item, env);
  const why = reason ? `\n\n🎯 <b>چرا مهم است</b>\n${escapeHtml(reason)}` : '';

  let background = '';
  if (Array.isArray(item.background) && item.background.length) {
    const rows = item.background.slice(0, 2).map(row => {
      const url = safeLink(row && row.link);
      const headline = escapeHtml(clip(row && row.title, 80));
      return url && headline ? `• <a href="${url}">${headline}</a>` : '';
    }).filter(Boolean);
    if (rows.length) background = `\n\n🧵 <b>پیش‌زمینه</b>\n${rows.join('\n')}`;
  }

  let text = `${emoji} <b>${title}</b>\n${divider}\n\n${summary}${why}${background}${original}\n\n${divider}\n`;
  const link = safeLink(item.link);
  text += link ? `📰 منبع: <a href="${link}">${source}</a>` : `📰 منبع: ${source}`;
  if (hashtags) text += `\n🏷️ ${hashtags}`;
  const also = (item.also_sources || []).filter(s => s && s.source).slice(0, 4);
  if (also.length) text += `\n🔗 همچنین در: ${also.map(s => escapeHtml(clip(s.source, 40))).join('، ')}`;
  if (item.hn && item.hn.points) {
    text += `\n🔥 Hacker News: ${Number(item.hn.points)} امتیاز`;
    if (item.hn.comments) text += ` · ${Number(item.hn.comments)} نظر`;
  }
  if (untranslated) text += '\n\n🔤 <i>ترجمه نشده — متن اصلی</i>';
  if (item.confidence === 'rumor') text += '\n\n⚠️ <i>این خبر تایید رسمی نشده است</i>';
  const signature = String(env?.CHANNEL_SIGNATURE || '').trim();
  if (signature) text += `\n\n${escapeHtml(signature)}`;
  return text;
}

function formatChannelMessage(item, env) {
  const showOriginal = String(env?.SHOW_ORIGINAL_TEXT ?? 'true').toLowerCase() !== 'false'
    && item.lang && item.lang !== 'fa' && item.translated !== false && !!item.title;

  // اولویت: بلوک متن اصلی حفظ شود؛ بعد کوتاه‌ترش کنیم و در آخر حذفش کنیم.
  const candidates = [];
  for (const n of [700, 400, 200, 80, 0]) {
    if (showOriginal) candidates.push([n, true]);
    candidates.push([n, false]);
  }
  for (const [n, orig] of candidates) {
    const text = renderChannelMessage(item, env, n, orig);
    if (text.length <= 4000) return text;
  }
  // عنوان/منبع/هشتگ غیرعادی بلند: هشتگ‌ها را هم کم کن
  for (const tagCount of [8, 4, 0]) {
    for (const n of [80, 0]) {
      const text = renderChannelMessage(item, env, n, false, tagCount);
      if (text.length <= 4000) return text;
    }
  }
  return renderChannelMessage(item, env, 0, false, 0);
}

// ───────── آینهٔ بله: فقط ارسال؛ شکست = حذف (بدون صف و بدون تلاش مجدد) ─────────
function formatBaleMessage(item) {
  const untranslated = item.translated === false;
  let emoji = item.admin_action === 'featured' ? '⭐️' : item.importance === 'high' ? '🔥' : '🤖';
  if (item.category === 'fast_track') emoji = '📡';
  const lines = [`${emoji} ${(item.title_fa || item.title || '').trim()}`, ''];
  const summary = clip(item.summary_fa, 700);
  if (summary) lines.push(summary, '');
  if (item.lang && item.lang !== 'fa' && !untranslated && item.title) lines.push(`🌐 ${clip(item.title, 150)}`, '');
  lines.push(`📰 منبع: ${item.source || ''}`);
  if (item.link) lines.push(`🔗 ${item.link}`);
  const tags = (item.hashtags || []).map(h => '#' + h).join(' ');
  if (tags) lines.push(tags);
  if (untranslated) lines.push('', '🔤 ترجمه نشده — متن اصلی');
  if (item.confidence === 'rumor') lines.push('', '⚠️ این خبر تایید رسمی نشده است');
  return lines.join('\n').slice(0, 4000);
}

async function sendBale(env, item) {
  const token = (env.BALE_BOT_TOKEN || '').trim();
  const chat = (env.BALE_CHANNEL_ID || '').trim();
  if (!token || !chat) return false;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    const res = await fetch(`https://tapi.bale.ai/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chat, text: formatBaleMessage(item) }),
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    const data = await res.json().catch(() => ({}));
    if (!data.ok) console.log('[bale] rejected');
    return !!data.ok;
  } catch (e) {
    console.log('[bale] error');
    return false;
  }
}

/** ارسال یک متن ساده به بله (برای خوش‌آمد و مواردی که قالب خبر ندارند) */
async function sendBaleText(env, text) {
  const token = (env.BALE_BOT_TOKEN || '').trim();
  const chat = (env.BALE_CHANNEL_ID || '').trim();
  if (!token || !chat || !text) return false;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    let res;
    try {
      res = await fetch(`https://tapi.bale.ai/bot${token}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chat, text: String(text).slice(0, 4000) }),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    const data = await res.json().catch(() => ({}));
    if (!data.ok) console.log('[bale] text rejected');
    return !!data.ok;
  } catch (e) {
    console.log('[bale] text error');
    return false;
  }
}

async function publishItem(env, BASE, CHANNEL_ID, ADMIN_ID, item) {
  const state = await getJSON(env, 'state', {});
  migrateState(state);
  if (state.paused) {
    console.log('[publishItem] Bot is paused');
    await sendMessage(BASE, ADMIN_ID, '⏸️ ربات متوقف است — خبر در صف «آماده انتشار» ماند');
    return;
  }

  if (isScheduledFuture(item)) {
    await sendMessage(BASE, ADMIN_ID, '⏰ زمان این خبر نرسیده — در صف «آماده انتشار» می‌ماند');
    return;
  }

  const today = new Date().toISOString().slice(0, 10);
  if (state.daily_post_date !== today) {
    state.daily_post_date = today;
    state.daily_post_count = 0;
  }

  const maxPosts = Number(env.MAX_POSTS_PER_RUN) || 15;
  if (state.daily_post_count >= maxPosts) {
    console.log('[publishItem] Run limit reached');
    await sendMessage(BASE, ADMIN_ID, `📛 سقف ${maxPosts} پست این راند پر شد — خبر در صف می‌ماند`);
    return;
  }

  const text = formatChannelMessage(item, env);

  const publishedLog = await getJSON(env, 'published', []);
  const key = itemKey(item);
  if (key && publishedLog.some(p => p.published && itemKey(p) === key)) {
    console.log('[publishItem] Already published, skipping');
    await sendMessage(BASE, ADMIN_ID, 'ℹ️ این خبر قبلاً منتشر شده بود');
    return;
  }

  const result = await sendMessage(BASE, CHANNEL_ID, text);
  if (result && result.ok) {
    state.daily_post_count++;
    await saveJSON(env, 'state', state);
    const msgId = result.result && result.result.message_id;
    publishedLog.push({ ...item, published: true, published_at: new Date().toISOString(),
      ...(msgId ? { message_id: msgId } : {}) });
    await saveJSON(env, 'published', publishedLog);
    // 📤 دکمهٔ اشتراک‌گذاری (لینک پست کانال، وگرنه منبع)
    if (msgId && String(env.SHARE_BUTTON_ENABLED || 'true').toLowerCase() !== 'false') {
      const handle = String(CHANNEL_ID || '').startsWith('@') ? String(CHANNEL_ID).slice(1) : '';
      const link = handle ? `https://t.me/${handle}/${msgId}` : (item.link || '');
      if (link) {
        const share = `https://t.me/share/url?url=${encodeURIComponent(link)}` +
          `&text=${encodeURIComponent(String(item.title_fa || item.title || 'خبر AI').slice(0, 120))}`;
        try {
          await telegramCall(BASE, 'editMessageReplyMarkup', {
            chat_id: CHANNEL_ID, message_id: msgId,
            reply_markup: { inline_keyboard: [[{ text: '📤 فرستادن به دوست', url: share }]] },
          });
        } catch (e) { /* نادیده */ }
      }
    }
    // از صف approved حذف کن تا دوباره منتشر نشه
    const approvedList = await getJSON(env, 'approved', []);
    await saveJSON(env, 'approved', approvedList.filter(i => itemKey(i) !== key));
    console.log('[publishItem] Published:', item.title_fa?.substring(0, 50));
    if (item.pin && msgId) {
      const pinned = await telegramCall(BASE, 'pinChatMessage', {
        chat_id: CHANNEL_ID, message_id: msgId, disable_notification: true,
      });
      if (pinned && pinned.ok) console.log('[publishItem] Pinned in channel');
    }
    // آخرین کار: همهٔ ذخیره‌ها انجام شده؛ شکست بله/بلو‌اسکای = حذف، بدون صف و بدون اثر بر تلگرام
    try { await sendBale(env, item); } catch (e) { /* نادیده */ }
    try { await sendBluesky(env, item); } catch (e) { /* نادیده */ }
  } else {
    console.log('[publishItem] Failed to publish:', result?.error_code, result?.description || '');
    if (result && result.error_code === 400) {
      // پیام از نظر تلگرام نامعتبر است (HTML/URL)؛ ماندنش در صف یعنی تلاش بی‌پایان
      // هر ساعت و بلعیدن یک اسلات انتشار.
      const approvedList = await getJSON(env, 'approved', []);
      await saveJSON(env, 'approved', approvedList.filter(i => itemKey(i) !== key));
      await sendMessage(BASE, ADMIN_ID, '⚠️ یک خبر به‌خاطر خطای قالب/لینک (400) از صف حذف شد.');
    }
  }
}

async function handleRun(env, BASE, CHANNEL_ID, ADMIN_ID) {
  console.log('[handleRun] Started');
  const approved = await getJSON(env, 'approved', []);
  const state = await getJSON(env, 'state', {});
  migrateState(state);
  console.log('[handleRun] Approved items:', approved.length);
  console.log('[handleRun] State:', JSON.stringify(state));

  if (state.paused) {
    console.log('[handleRun] Bot is paused, skipping');
    return new Response(JSON.stringify({ ok: true, skipped: 'paused' }), { headers: { 'Content-Type': 'application/json' } });
  }

  const today = new Date().toISOString().slice(0, 10);
  if (state.daily_post_date !== today) {
    state.daily_post_date = today;
    state.daily_post_count = 0;
  }

  const MAX_POSTS = Number(env.MAX_POSTS_PER_RUN) || 15;
  let published = 0;
  const publishedLog = await getJSON(env, 'published', []);
  const publishedIds = new Set(publishedLog.filter(p => p.published).map(p => itemKey(p)));

  const importanceOrder = { high: 0, medium: 1, low: 2 };
  approved.sort((a, b) => {
    if (a.admin_action === 'featured' && b.admin_action !== 'featured') return -1;
    if (b.admin_action === 'featured' && a.admin_action !== 'featured') return 1;
    return (importanceOrder[a.importance] || 2) - (importanceOrder[b.importance] || 2);
  });

  for (const item of approved) {
    if (state.daily_post_count >= MAX_POSTS) {
      console.log('[handleRun] Run limit reached');
      break; // بقیه در approved می‌مانند
    }

    const text = formatChannelMessage(item, env);

    const key = itemKey(item);
    if (key && publishedIds.has(key)) {
      console.log('[handleRun] Already published, skipping:', key);
      continue;
    }
    if (isScheduledFuture(item)) {
      console.log('[handleRun] Scheduled later, keeping:', key);
      continue;
    }

    console.log('[handleRun] Publishing:', item.title_fa?.substring(0, 50));
    const result = await sendMessage(BASE, CHANNEL_ID, text);
    console.log('[handleRun] Send result:', JSON.stringify(result));
    if (result && result.ok) {
      state.daily_post_count++;
      published++;
      const msgId = result.result && result.result.message_id;
      publishedLog.push({ ...item, published: true, published_at: new Date().toISOString(),
        ...(msgId ? { message_id: msgId } : {}) });
      if (key) publishedIds.add(key);
      console.log('[handleRun] Published successfully');
      try { await sendBluesky(env, item); } catch (e) { /* نادیده */ }
    } else {
      console.log('[handleRun] Failed to publish');
    }
  }

  // وضعیت را دوباره بخوان: اگر ادمین وسط انتشار /pause زده یا «انتشار خودکار» را
  // خاموش کرده، نوشتن نسخهٔ کهنه آن را بی‌صدا برمی‌گرداند. فقط شمارنده‌های همین اجرا منتقل می‌شوند.
  const freshState = await getJSON(env, 'state', {});
  migrateState(freshState);
  freshState.daily_post_count = state.daily_post_count;
  freshState.daily_post_date = state.daily_post_date;
  await saveJSON(env, 'state', freshState);
  if (published > 0) await saveJSON(env, 'published', publishedLog);

  // فقط خبرهای منتشرشده را از صف حذف کن؛ تاییدهای هم‌زمان ادمین از دست نروند.
  const freshApproved = await getJSON(env, 'approved', []);
  const kept = freshApproved.filter(i => {
    const k = itemKey(i);
    return !(k && publishedIds.has(k));
  });
  await saveJSON(env, 'approved', kept);

  return new Response(JSON.stringify({ ok: true, published, remaining: kept.length }), { headers: { 'Content-Type': 'application/json' } });
}

// === Helpers ===

/**
 * آینهٔ Bluesky — بعد از هر انتشار موفق، متن کوتاه (≤۲۸۰) به Bluesky می‌رود.
 * اگر کلیدها نباشند یا هر خطایی رخ دهد، فقط لاگ می‌شود و انتشار تلگرام دست نمی‌خورد.
 */
/**
 * متن Bluesky: عنوان + خلاصه + لینک با اولویت حفظ لینک.
 * سقف ۲۸۰ کاراکتر است و لینک هم جزو متن؛ اگر ساده ببریم لینک ناقص و بی‌فایده می‌شود.
 */
function formatBlueskyText(item) {
  const MAX = 280;
  const title = String(item.title_fa || item.title || '').trim();
  const summary = String(item.summary_fa || '').trim();
  const link = String(item.link || '').trim();

  if (!link) return clip([title, summary].filter(Boolean).join('\n\n'), MAX);
  if (link.length >= MAX) return link.slice(0, MAX);

  let budget = MAX - link.length - 2;
  const head = [];
  if (title) {
    const t = clip(title, budget);
    head.push(t);
    budget -= t.length;
  }
  if (summary && budget > 3) {
    budget -= 2;
    const s = clip(summary, budget);
    if (s) head.push(s);
  }
  if (!head.length) return link;
  return [...head, link].join('\n\n');
}

async function sendBluesky(env, item) {
  const handle = (env.BLUESKY_HANDLE || '').trim();
  const password = (env.BLUESKY_APP_PASSWORD || '').trim();
  if (!handle || !password) return false;
  const text = formatBlueskyText(item);
  if (!text) return false;
  try {
    const ctrl1 = new AbortController();
    const timer1 = setTimeout(() => ctrl1.abort(), 8000);
    let login;
    try {
      login = await fetch('https://bsky.social/xrpc/com.atproto.server.createSession', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identifier: handle, password }),
        signal: ctrl1.signal,
      });
    } finally {
      clearTimeout(timer1);
    }
    const session = await login.json().catch(() => null);
    if (!session || !session.accessJwt || !session.did) {
      console.log('[bsky] login failed');
      return false;
    }
    const ctrl2 = new AbortController();
    const timer2 = setTimeout(() => ctrl2.abort(), 8000);
    let record;
    try {
      record = await fetch('https://bsky.social/xrpc/com.atproto.repo.createRecord', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${session.accessJwt}` },
        body: JSON.stringify({
          repo: session.did,
          collection: 'app.bsky.feed.post',
          record: { $type: 'app.bsky.feed.post', text, createdAt: new Date().toISOString() },
        }),
        signal: ctrl2.signal,
      });
    } finally {
      clearTimeout(timer2);
    }
    return record.status === 200;
  } catch (e) {
    console.log('[bsky] error:', e?.message || e);
    return false;
  }
}

/**
 * گزارش‌های بلند ادمین (/feeds, /sources, /runs, ...): تلگرام پیام بالای ۴۰۹۶ کاراکتر را با
 * «message is too long» رد می‌کند و ادمین هیچ چیز نمی‌بیند. اینجا روی مرز خط تکه می‌شود؛
 * هر خط گزارش‌ها تگ‌هایش را خودش می‌بندد، پس HTML نصفه نمی‌ماند.
 * (برای پست‌های کانال استفاده نشود؛ آن‌ها برش و قالب مخصوص خودشان را دارند.)
 */
function splitLong(text, limit = 3800) {
  const out = [];
  let cur = '';
  for (let line of String(text ?? '').split('\n')) {
    while (line.length > limit) {          // خط تکی بسیار بلند
      if (cur) { out.push(cur); cur = ''; }
      out.push(line.slice(0, limit));
      line = line.slice(limit);
    }
    if (cur && cur.length + 1 + line.length > limit) { out.push(cur); cur = line; }
    else cur = cur ? cur + '\n' + line : line;
  }
  if (cur) out.push(cur);
  return out.length ? out : [''];
}

async function sendLong(BASE, chatId, text, replyMarkup = null) {
  const parts = splitLong(text);
  let res = null;
  for (let i = 0; i < parts.length; i++) {
    res = await sendMessage(BASE, chatId, parts[i], i === parts.length - 1 ? replyMarkup : null);
  }
  return res;
}

async function sendMessage(BASE, chatId, text, replyMarkup = null) {
  const data = { chat_id: chatId, text, parse_mode: 'HTML' };
  if (replyMarkup) data.reply_markup = replyMarkup;

  // تایم‌اوت: درخواست معلق نباید وبهوک را قفل کند (تلگرام بعدش update را دوباره می‌فرستد).
  // روی 429 یک‌بار با احترام به retry_after تلاش دوباره می‌شود.
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 10000);
      let resp;
      try {
        resp = await fetch(`${BASE}/sendMessage`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
          signal: ctrl.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      const out = await resp.json().catch(() => null);
      if (out && out.ok) return out;
      if (out && out.error_code === 429 && attempt === 0) {
        const wait = Math.min(Math.max(Number(out?.parameters?.retry_after) || 1, 1), 3);
        await new Promise(r => setTimeout(r, wait * 1000));
        continue;
      }
      if (out) {
        console.error('[sendMessage] failed:', out.error_code, out.description || '');
        return out;
      }
      return { ok: false, error_code: resp.status || 0, error: 'invalid response' };
    } catch (e) {
      console.error('sendMessage error:', e?.message || e);
      return { ok: false, error_code: 0, error: 'network' };
    }
  }
  return { ok: false, error_code: 429, error: 'rate limited' };
}

/** آخرین پست منتشرشدهٔ قابل حذف/ویرایش (حداکثر ۴۸ ساعت پیش) */
const EDIT_WINDOW_MS = 48 * 3600 * 1000;

async function lastPublishedItem(env) {
  const log = await getJSON(env, 'published', []);
  const now = Date.now();
  for (let i = log.length - 1; i >= 0; i--) {
    const p = log[i];
    if (!p || !p.published || p.deleted || !p.message_id) continue;
    const at = Date.parse(p.published_at || '');
    if (!Number.isFinite(at) || now - at > EDIT_WINDOW_MS) continue;
    return { log, index: i, item: p };
  }
  return { log, index: -1, item: null };
}

async function telegramCall(BASE, method, payload) {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 10000);
    let resp;
    try {
      resp = await fetch(`${BASE}/${method}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    return await resp.json().catch(() => null);
  } catch (e) {
    console.error(`${method} error:`, e?.message || e);
    return null;
  }
}

async function doUndo(env, BASE, CHANNEL_ID, ADMIN_ID) {
  const { log, index, item } = await lastPublishedItem(env);
  if (index < 0) {
    await sendMessage(BASE, ADMIN_ID, '🗑 پستی برای حذف پیدا نشد.');
    return false;
  }
  const res = await telegramCall(BASE, 'deleteMessage', {
    chat_id: CHANNEL_ID, message_id: item.message_id,
  });
  if (!res || !res.ok) {
    await sendMessage(BASE, ADMIN_ID, `⚠️ حذف نشد: ${(res && res.description) || 'خطای تلگرام'}`);
    return false;
  }
  item.deleted = true;
  item.deleted_at = new Date().toISOString();
  await saveJSON(env, 'published', log);
  await sendMessage(BASE, ADMIN_ID, `✅ حذف شد: «${escapeHtml(item.title_fa || item.title || '?')}»`);
  return true;
}

async function applyPublishedEdit(env, BASE, CHANNEL_ID, ADMIN_ID, messageId, newSummary) {
  const log = await getJSON(env, 'published', []);
  const item = log.find(p => p && p.message_id === messageId);
  if (!item) {
    await updateState(env, { _editing_pub: null });
    await sendMessage(BASE, ADMIN_ID, '⚠️ پست پیدا نشد.');
    return false;
  }
  item.summary_fa = String(newSummary || '').trim().slice(0, 700);
  item.edited_at = new Date().toISOString();
  const res = await telegramCall(BASE, 'editMessageText', {
    chat_id: CHANNEL_ID, message_id: messageId, parse_mode: 'HTML',
    text: formatChannelMessage(item, env),
  });
  await updateState(env, { _editing_pub: null });
  if (res && res.ok) {
    await saveJSON(env, 'published', log);
    await sendMessage(BASE, ADMIN_ID, '✅ پست کانال ویرایش شد.');
    return true;
  }
  await sendMessage(BASE, ADMIN_ID, `⚠️ ویرایش نشد: ${(res && res.description) || 'خطای تلگرام'}`);
  return false;
}

/**
 * منوی /start را با بنر می‌فرستد؛ اگر تلگرام نتوانست عکس را بگیرد،
 * به پیام متنی ساده برمی‌گردد تا منو هرگز از دست نرود.
 */
async function sendStartMessage(BASE, chatId, text, replyMarkup = null, bannerUrl = START_BANNER_URL) {
  if (bannerUrl) {
    try {
      const payload = { chat_id: chatId, photo: bannerUrl, caption: text, parse_mode: 'HTML' };
      if (replyMarkup) payload.reply_markup = replyMarkup;
      const resp = await fetch(`${BASE}/sendPhoto`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await resp.json().catch(() => null);
      if (data && data.ok) return data;
      console.error('[start] sendPhoto failed:', (data && data.description) || resp.status);
    } catch (e) {
      console.error('[start] sendPhoto error:', e);
    }
  }
  return sendMessage(BASE, chatId, text, replyMarkup);
}

function formatTipPost(tip) {
  const who = escapeHtml(String(tip.user || 'مخاطب کانال').slice(0, 40));
  const body = escapeHtml(String(tip.text || '').slice(0, 600));
  return '📣 <b>از مخاطبان کانال</b>\n' + '─'.repeat(20) + '\n\n' +
    `<blockquote>${body}</blockquote>\n\n— ${who}\n🏷️ #از_مخاطبان`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function fmtViews(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return '—';
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(Math.round(n));
}

/**
 * «کلید مرده»: اگر بیش از ۳ ساعت هیچ اجرای موفقی ثبت نشده باشد، به ادمین هشدار بده.
 * heartbeat فقط در پایان اجرای موفق نوشته می‌شود (src/storage.py)؛ پس کهنه‌بودنش
 * یعنی پایپ‌لاین یا GitHub Actions از کار افتاده. حداکثر هر ۳ ساعت یک هشدار.
 */
async function checkHeartbeat(env) {
  try {
    const ADMIN_ID = Number(env.ADMIN_ID);
    if (!env.BOT_TOKEN || !ADMIN_ID) return;
    const hb = await getJSON(env, 'heartbeat', {});
    const at = Date.parse(hb.at || '');
    if (!Number.isFinite(at)) return;
    const ageH = (Date.now() - at) / 3600000;
    if (ageH < 3) return;

    const alerts = await getJSON(env, 'alert_state', {});
    const last = Date.parse(alerts.heartbeat_at || '');
    if (Number.isFinite(last) && Date.now() - last < 3 * 3600000) return;

    const BASE = `https://api.telegram.org/bot${env.BOT_TOKEN}`;
    await sendMessage(BASE, ADMIN_ID,
      `⚠️ <b>پایپ‌لاین از کار افتاده؟</b>\n\n` +
      `آخرین اجرای موفق: <b>${Math.floor(ageH)} ساعت پیش</b>\n` +
      `یعنی چند اجرای ساعتی انجام نشده است.\n` +
      `GitHub Actions و Secrets را چک کن، بعد /health را بفرست.`);
    await saveJSON(env, 'alert_state', { ...alerts, heartbeat_at: new Date().toISOString() });
    console.log('[heartbeat] stale alert sent');
  } catch (e) {
    console.error('[heartbeat] alert failed:', (e && e.message) || e);
  }
}

async function answerCallback(BASE, cbId, text, showAlert = false) {
  try {
    await fetch(`${BASE}/answerCallbackQuery`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ callback_query_id: cbId, text, show_alert: showAlert })
    });
  } catch (e) {
    console.error('answerCallback error:', e);
  }
}

async function dispatchGithubWorkflow(env) {
  try {
    const resp = await fetch(
      `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/pipeline.yml/dispatches`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${env.GITHUB_TOKEN}`,
          'Accept': 'application/vnd.github+json',
          'User-Agent': 'ai-news-bot-worker',
          'Content-Type': 'application/json'
        },
        // trigger=cron → ورک‌فلو بدون --force اجرا می‌شود و فاصلهٔ جمع‌آوری را رعایت می‌کند (اجرای تکراری بی‌اثر می‌ماند)
        body: JSON.stringify({ ref: 'main', inputs: { trigger: 'cron' } })
      }
    );
    console.log('[scheduled] GitHub dispatch status:', resp.status);
  } catch (e) {
    console.error('[scheduled] dispatch failed:', e);
  }
}

async function sendBackup(env, BASE, ADMIN_ID) {
  try {
    const keys = {};
    const names = ['state', 'seen', 'pending', 'approved', 'published', 'source_stats',
      'feed_health', 'run_log', 'brief_state', 'views_state', 'weekly_state', 'stories',
      'reactions', 'edu_state', 'digest_state', 'arxiv_state', 'subscribers', 'tips', 'tip_rate'];
    for (const key of names) {
      const value = await getJSON(env, key, null);
      if (value !== null) keys[key] = value;
    }
    const body = JSON.stringify({
      format: 'ai-news-bot-kv', version: 1,
      generated_at: new Date().toISOString(), keys,
    }, null, 1);
    const form = new FormData();
    form.append('chat_id', String(ADMIN_ID));
    form.append('caption', '💾 پشتیبان KV — بازیابی: python -m src.backup --restore <file>');
    form.append('document', new Blob([body], { type: 'application/json' }),
      `ai-news-bot-backup-${new Date().toISOString().slice(0, 10)}.json`);
    const res = await fetch(`${BASE}/sendDocument`, { method: 'POST', body: form });
    const data = await res.json().catch(() => ({}));
    await sendMessage(BASE, ADMIN_ID, data.ok ? '✅ پشتیبان ارسال شد.' : '⚠️ ارسال پشتیبان نشد.');
  } catch (e) {
    await sendMessage(BASE, ADMIN_ID, '⚠️ ساخت پشتیبان ناموفق بود.');
  }
}

async function recordReactionCount(env, update) {
  try {
    const mid = Number(update.message_id);
    if (!mid) return;
    const by = {};
    let total = 0;
    for (const r of (update.reactions || [])) {
      const n = Number(r.total_count || 0);
      if (n <= 0) continue;
      const emoji = String(r.type?.emoji || '?');
      by[emoji] = (by[emoji] || 0) + n;
      total += n;
    }
    if (!total) return;
    const data = await getJSON(env, 'reactions', {});
    data[String(mid)] = { total, by, at: new Date().toISOString() };
    // فقط ۵۰۰ پست آخر نگه داشته شود
    const entries = Object.entries(data).sort((a, b) =>
      String(b[1]?.at || '').localeCompare(String(a[1]?.at || ''))).slice(0, 500);
    await saveJSON(env, 'reactions', Object.fromEntries(entries));
  } catch (e) {
    console.error('[reactions] failed:', (e && e.message) || e);
  }
}

async function getJSON(env, key, defaultValue) {
  try {
    const val = await env.KV.get(key);
    return val ? JSON.parse(val) : defaultValue;
  } catch {
    return defaultValue;
  }
}

async function saveJSON(env, key, value) {
  await env.KV.put(key, JSON.stringify(value));
}

async function updateState(env, updates) {
  const state = await getJSON(env, 'state', {});
  migrateState(state);
  Object.assign(state, updates);
  await saveJSON(env, 'state', state);
}

/** وضعیت سلامت — هم برای GET /health و هم برای دستور /health در ربات */
async function healthSnapshot(env) {
  const started = Date.now();
  let kvOk = true;
  try {
    await env.KV.get('health_ping');
  } catch (e) {
    kvOk = false;
  }
  const [state, pending, approved, published, heartbeat, edu] = await Promise.all([
    getJSON(env, 'state', {}),
    getJSON(env, 'pending', []),
    getJSON(env, 'approved', []),
    getJSON(env, 'published', []),
    getJSON(env, 'heartbeat', {}),
    getJSON(env, 'edu_state', {}),
  ]);
  const todayKey = new Date(Date.now() + 210 * 60000).toISOString().slice(0, 10);
  return {
    ok: kvOk,
    kv: kvOk,
    ms: Date.now() - started,
    stage: state.current_stage || 'idle',
    paused: !!state.paused,
    lastHeartbeat: heartbeat.at || null,
    lastRunPublished: heartbeat.published ?? null,
    queue: { pending: pending.length, approved: approved.length },
    modulesFailing: Object.entries(heartbeat.modules || {}).filter(([, m]) => m && m.ok === false).map(([n]) => n),
    published: published.filter(p => p && p.published).length,
    edu: {
      queue: (edu.queue || []).length,
      today: Number((edu.pub_count || {})[todayKey] || 0),
    },
    ts: new Date().toISOString(),
  };
}

/**
 * تفسیر ورودی «انتشار خودکار» از متن دستور یا callback_data.
 * true = روشن، false = خاموش، null = نامشخص (باید دکمه نشان داده شود).
 * پشتیبانی: /auto on|off، /auto_on، /auto-off، /autoon، auto:on، auto_publish:off، ...
 */
function parseAutoChoice(raw) {
  const s = String(raw || '').trim().toLowerCase();
  if (!s) return null;
  if (s.includes('روشن') && !s.includes('خاموش')) return true;
  if (s.includes('خاموش') && !s.includes('روشن')) return false;
  // حذف @نام_ربات و همهٔ جداکننده‌ها: /auto@MyBot on → autoon
  const cleaned = s.replace(/@[a-z0-9_]+/g, '').replace(/[^a-z0-9]+/g, '');
  if (['autoon', 'autopublishon', 'enableauto', 'autoenable', 'autotrue', 'auto1'].includes(cleaned)) {
    return true;
  }
  if (['autooff', 'autopublishoff', 'disableauto', 'autodisable', 'autofalse', 'auto0'].includes(cleaned)) {
    return false;
  }
  return null;
}

async function setAutoPublish(env, BASE, ADMIN_ID, cbId, enabled) {
  await updateState(env, {
    auto_publish_enabled: enabled,
    auto_publish_set_by_admin: true
  });
  const label = enabled ? 'روشن ✅' : 'خاموش ❌';
  if (cbId) await answerCallback(BASE, cbId, `انتشار خودکار: ${label}`);
  await sendMessage(BASE, ADMIN_ID, `انتشار خودکار: ${label}`);
}

function migrateState(state) {
  // مقدار قدیمی auto_publish نادیده گرفته می‌شود (ممکن است از تست‌های قدیمی false مانده باشد).
  if (state.auto_publish_enabled === undefined) {
    state.auto_publish_enabled = true;
  } else if (state.auto_publish_enabled === false && state.auto_publish_set_by_admin !== true) {
    // falseِ قدیمی/تصادفی → یک‌بار روشن شود؛ خاموش‌کردن دستی ادمین محفوظ می‌ماند.
    state.auto_publish_enabled = true;
  }
}

/** کلید یکتای خبر — بعضی آیتم‌های قدیمی «hash» دارن، جدیدها «id» */
function itemKey(item) {
  return item.id || item.hash || '';
}

/** خبر زمان‌بندی‌شده‌ای که هنوز موعدش نرسیده */
function isScheduledFuture(item) {
  const at = Date.parse((item && item.schedule_at) || '');
  return Number.isFinite(at) && at > Date.now();
}
