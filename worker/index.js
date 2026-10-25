/**
 * Cloudflare Worker — ربات تلگرام همیشه فعال
 * آمار و وضعیت کامل
 */

export default {
  async fetch(request, env) {
    const BOT_TOKEN = env.BOT_TOKEN;
    const CHANNEL_ID = env.CHANNEL_ID;
    const ADMIN_ID = Number(env.ADMIN_ID);
    const BASE = `https://api.telegram.org/bot${BOT_TOKEN}`;

    const url = new URL(request.url);
    const path = url.pathname;

    if (path === '/webhook' && request.method === 'POST') {
      // اگه WEBHOOK_SECRET تنظیم شده باشه، هدر تلگرام رو بررسی کن
      if (env.WEBHOOK_SECRET) {
        const got = request.headers.get('X-Telegram-Bot-Api-Secret-Token') || '';
        if (got !== env.WEBHOOK_SECRET) {
          return new Response('Forbidden', { status: 403 });
        }
      }

      let update;
      try {
        update = await request.json();
      } catch {
        return new Response('Bad Request', { status: 400 });
      }

      if (update.callback_query) {
        await handleCallback(update.callback_query, env, BASE, CHANNEL_ID, ADMIN_ID);
      } else if (update.message) {
        await handleMessage(update.message, env, BASE, CHANNEL_ID, ADMIN_ID);
      }

      return new Response('OK');
    }

    if (path === '/run' && request.method === 'POST') {
      const auth = request.headers.get('Authorization') || '';
      const token = auth.replace('Bearer ', '');
      if (!env.WORKER_SECRET || token !== env.WORKER_SECRET) {
        return new Response(JSON.stringify({ ok: false, error: 'Unauthorized' }), {
          status: 403,
          headers: { 'Content-Type': 'application/json' }
        });
      }
      const result = await handleRun(env, BASE, CHANNEL_ID, ADMIN_ID);
      return result;
    }

    return new Response('OK');
  },

  /**
   * اختیاری: اجرای ساعت‌به‌ساعتِ ورک‌فلوی گیت‌هاب از طریق Cloudflare.
   * فقط وقتی کار می‌کند که GITHUB_TOKEN و GITHUB_REPO در secrets تنظیم شده باشند.
   * (برای فعال‌سازی: Settings → Triggers → Cron = 17 * * * *)
   */
  async scheduled(event, env, ctx) {
    if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return;
    ctx.waitUntil(dispatchGithubWorkflow(env));
  }
};

async function handleCallback(cb, env, BASE, CHANNEL_ID, ADMIN_ID) {
  // Never trust callback data without checking the Telegram user who clicked it.
  if (Number(cb.from?.id) !== ADMIN_ID) {
    await answerCallback(BASE, cb.id, '⛔️ دسترسی ندارید', true);
    return;
  }
  const data = cb.data || '';
  const cbId = cb.id;
  const parts = data.split(':');
  const action = parts[0];
  const itemHash = parts[1] || '';

  const pending = await getJSON(env, 'pending', []);
  const approved = await getJSON(env, 'approved', []);

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
  } else if (action === 'pause') {
    await updateState(env, { paused: true });
    await answerCallback(BASE, cbId, '⏸️ متوقف');
  } else if (action === 'resume') {
    await updateState(env, { paused: false });
    await answerCallback(BASE, cbId, '▶️ فعال');
  }
}

async function handleMessage(msg, env, BASE, CHANNEL_ID, ADMIN_ID) {
  const text = (msg.text || '').trim();
  const chatId = msg.chat.id;

  if (chatId !== ADMIN_ID) return;

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

  if (text === '/start') {
    await sendMessage(BASE, ADMIN_ID,
      '🤖 <b>ربات مدیریت کانال اخبار AI</b>\n\n' +
      'دستورات:\n' +
      '/status - وضعیت سیستم\n' +
      '/pending - اخبار در انتظار تایید\n' +
      '/pause - توقف انتشار\n' +
      '/resume - ادامه انتشار\n' +
      '/auto on|off - انتشار خودکار\n' +
      '/clear - پاک‌کردن کل صف\n' +
      '/sources - اعتبار پویای منابع\n' +
      '/feeds - سلامت فیدهای خبری\n' +
      '/runs - آمار اجراهای اخیر (چند خبر پیدا شد)\n' +
      '/stats - آمار کامل'
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
      (topSources ? `━━━━━━━━━━━━━━\n🏆 برترین منابع:\n${topSources}` : '')
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
    await sendMessage(BASE, ADMIN_ID, runsReport(await getJSON(env, 'run_log', []), env));
  } else if (text === '/feeds') {
    await sendMessage(BASE, ADMIN_ID, feedsReport(await getJSON(env, 'feed_health', {})));
  } else if (text === '/sources') {
    await sendMessage(BASE, ADMIN_ID, sourcesReport(await getJSON(env, 'source_stats', {}), env));
  } else if (text === '/pause') {
    await updateState(env, { paused: true });
    await sendMessage(BASE, ADMIN_ID, '⏸️ انتشار متوقف شد');
  } else if (text === '/resume') {
    await updateState(env, { paused: false });
    await sendMessage(BASE, ADMIN_ID, '▶️ انتشار ادامه یافت');
  } else if (text.startsWith('/auto')) {
    const parts = text.split(/\s+/);
    if (parts.length > 1 && ['on', 'off'].includes(parts[1])) {
      const val = parts[1] === 'on';
      await updateState(env, { auto_publish_enabled: val, auto_publish_set_by_admin: true });
      await sendMessage(BASE, ADMIN_ID, `انتشار خودکار: ${val ? 'روشن ✅' : 'خاموش ❌'}`);
    } else {
      await sendMessage(BASE, ADMIN_ID, 'استفاده: /auto on یا /auto off');
    }
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
        (r.untranslated ? ` | 🔤 ${r.untranslated} ترجمه‌نشده` : ''));
    } else lines.push(`${tag} ${hhmm} — جمع‌آوری انجام نشد | منتشر: ${r.published || 0}`);
  }
  lines.push('\n🤖 خودکار | 👀 نیازمند تایید شما | ❌ ردشده (کم‌امتیاز/کلمه مشکوک)');
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
  for (const [n, h] of good) {
    lines.push(`✅ ${escapeHtml(n)}: ${h.items || 0} آیتم، ${h.new || 0} جدید` + (h.filtered ? `، ${h.filtered} ردشده با فیلتر` : '') +
      (h.old ? `، ${h.old} کهنه` : '') + (h.newest ? ` | آخرین خبر فید: ${agoText(h.newest)}` : ''));
    for (const t of (h.filtered_titles || []).slice(0, 3)) lines.push(`      🚫 ${escapeHtml(t)}`);
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

function formatChannelMessage(item, env) {
  let emoji = '🤖';
  let divider = '─'.repeat(20);
  if (item.category === 'fast_track') emoji = '📡';
  else if (item.admin_action === 'featured') { emoji = '⭐️'; divider = '═'.repeat(20); }
  else if (item.importance === 'high') emoji = '🔥';
  else if (item.confidence === 'rumor') emoji = '⚠️';

  const untranslated = item.translated === false;
  const title = escapeHtml(item.title_fa || item.title || '');
  const summary = escapeHtml(clip(item.summary_fa, 700));
  const link = escapeHtml(item.link);
  const source = escapeHtml(item.source);
  const hashtags = (item.hashtags || []).map(h => '#' + escapeHtml(h)).join(' ');

  let original = '';
  const showOriginal = String(env?.SHOW_ORIGINAL_TEXT ?? 'true').toLowerCase() !== 'false';
  if (showOriginal && item.lang && item.lang !== 'fa' && !untranslated && item.title) {
    const oTitle = escapeHtml(clip(item.title, 200));
    const oSummary = escapeHtml(clip(item.summary, 500));
    const body = `<b>${oTitle}</b>` + (oSummary ? `\n\n${oSummary}` : '');
    original = `\n\n🌐 <b>متن اصلی</b>\n<blockquote expandable>${body}</blockquote>`;
  }

  let text = `${emoji} <b>${title}</b>\n${divider}\n\n${summary}${original}\n\n${divider}\n`;
  text += `📰 منبع: <a href="${link}">${source}</a>`;
  if (hashtags) text += `\n🏷️ ${hashtags}`;
  if (untranslated) text += '\n\n🔤 <i>ترجمه نشده — متن اصلی</i>';
  if (item.confidence === 'rumor') text += '\n\n⚠️ <i>این خبر تایید رسمی نشده است</i>';

  if (text.length > 4000 && original) text = text.replace(original, '');
  return text.length > 4000 ? text.slice(0, 4000) : text;
}

async function publishItem(env, BASE, CHANNEL_ID, ADMIN_ID, item) {
  const state = await getJSON(env, 'state', {});
  migrateState(state);
  if (state.paused) {
    console.log('[publishItem] Bot is paused');
    await sendMessage(BASE, ADMIN_ID, '⏸️ ربات متوقف است — خبر در صف «آماده انتشار» ماند');
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
    publishedLog.push({ ...item, published: true, published_at: new Date().toISOString() });
    await saveJSON(env, 'published', publishedLog);
    // از صف approved حذف کن تا دوباره منتشر نشه
    const approvedList = await getJSON(env, 'approved', []);
    await saveJSON(env, 'approved', approvedList.filter(i => itemKey(i) !== key));
    console.log('[publishItem] Published:', item.title_fa?.substring(0, 50));
  } else {
    console.log('[publishItem] Failed to publish');
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

    console.log('[handleRun] Publishing:', item.title_fa?.substring(0, 50));
    const result = await sendMessage(BASE, CHANNEL_ID, text);
    console.log('[handleRun] Send result:', JSON.stringify(result));
    if (result && result.ok) {
      state.daily_post_count++;
      published++;
      publishedLog.push({ ...item, published: true, published_at: new Date().toISOString() });
      if (key) publishedIds.add(key);
      console.log('[handleRun] Published successfully');
    } else {
      console.log('[handleRun] Failed to publish');
    }
  }

  await saveJSON(env, 'state', state);
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

async function sendMessage(BASE, chatId, text, replyMarkup = null) {
  const data = { chat_id: chatId, text, parse_mode: 'HTML' };
  if (replyMarkup) data.reply_markup = replyMarkup;
  try {
    const resp = await fetch(`${BASE}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    return await resp.json();
  } catch (e) {
    console.error('sendMessage error:', e);
    return null;
  }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
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
        body: JSON.stringify({ ref: 'main' })
      }
    );
    console.log('[scheduled] GitHub dispatch status:', resp.status);
  } catch (e) {
    console.error('[scheduled] dispatch failed:', e);
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
