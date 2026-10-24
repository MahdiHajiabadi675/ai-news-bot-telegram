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
    await answerCallback(BASE, cbId, found ? '❌ رد شد' : '❌ خبر پیدا نشد', !found);
  } else if (action === 'feature') {
    const idx = pending.findIndex(i => itemKey(i) === itemHash);
    if (idx !== -1) {
      const item = pending.splice(idx, 1)[0];
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

    // بودجه Gemini
    const gb = await getJSON(env, 'gemini_budget', {});
    const gbToday = gb.date === today ? (gb.count || 0) : 0;
    const gbMax = gb.max_per_day || 24;
    const gbLast = gb.last_call_at ? String(gb.last_call_at).slice(11, 16) : null;

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
      `🧠 Gemini امروز: ${gbToday}/${gbMax}` + (gbLast ? ` (آخرین: ${gbLast})` : '') + `\n` +
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
  const trust = item.source_trust || 3;
  const trustEmoji = trust >= 4 ? '🟢' : trust >= 3 ? '🟡' : '🔴';
  const score = item.score || 0;
  const scoreLabel = score >= 70 ? 'زیاد' : score >= 50 ? 'متوسط' : score >= 30 ? 'كم' : 'ضعیف';
  const scoreEmoji = score >= 70 ? '🟢' : score >= 50 ? '🟡' : score >= 30 ? '🟠' : '🔴';

  const id = itemKey(item);
  let text = `📰 <b>${escapeHtml(item.title_fa)}</b>\n\n`;
  text += `${escapeHtml(item.summary_fa)}\n\n`;
  text += `🆔 ID: <code>${escapeHtml(id || 'N/A')}</code>\n`;
  text += `📌 اهمیت: ${escapeHtml(item.importance)} | اعتبار: ${escapeHtml(item.confidence)}\n`;
  text += `${trustEmoji} منبع: ${escapeHtml(item.source)} (${trust}/5)\n`;
  text += `${scoreEmoji} اسکور: ${score}/100 (${scoreLabel})\n`;
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

  await sendMessage(BASE, ADMIN_ID, text, keyboard);
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

  if (state.daily_post_count >= maxPosts) {
    console.log('[publishItem] Run limit reached');
    await sendMessage(BASE, ADMIN_ID, `📛 سقف ${maxPosts} پست این راند پر شد — خبر در صف می‌ماند`);
    return;
  }

  let text = '';
  const title = escapeHtml(item.title_fa);
  const summary = escapeHtml(item.summary_fa);
  const source = escapeHtml(item.source);
  const link = escapeHtml(item.link);
  const hashtags = (item.hashtags || []).map(h => '#' + escapeHtml(h)).join(' ');
  if (item.category === 'fast_track') {
    text = `📡 <b>${title}</b>\n\n${summary}\n\n📰 منبع: <a href="${link}">${source}</a>\n🏷️ ${hashtags}`;
  } else if (item.admin_action === 'featured') {
    text = `⭐️ <b>${title}</b>\n\n${summary}\n\n📰 منبع: <a href="${link}">${source}</a>\n🏷️ ${hashtags}`;
  } else if (item.importance === 'high') {
    text = `🔥 <b>${title}</b>\n\n${summary}\n\n📰 منبع: <a href="${link}">${source}</a>\n🏷️ ${hashtags}`;
  } else {
    text = `🤖 <b>${title}</b>\n\n${summary}\n\n📰 منبع: <a href="${link}">${source}</a>\n🏷️ ${hashtags}`;
  }

  if (item.confidence === 'rumor') {
    text += '\n\n⚠️ <i>این خبر تایید رسمی نشده است</i>';
  }

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

    let text = '';
    const title = escapeHtml(item.title_fa);
    const summary = escapeHtml(item.summary_fa);
    const source = escapeHtml(item.source);
    const link = escapeHtml(item.link);
    const hashtags = (item.hashtags || []).map(h => '#' + escapeHtml(h)).join(' ');
    if (item.category === 'fast_track') {
      text = `📡 <b>${title}</b>\n\n${summary}\n\n📰 منبع: <a href="${link}">${source}</a>\n🏷️ ${hashtags}`;
    } else if (item.admin_action === 'featured') {
      text = `⭐️ <b>${title}</b>\n\n${summary}\n\n📰 منبع: <a href="${link}">${source}</a>\n🏷️ ${hashtags}`;
    } else if (item.importance === 'high') {
      text = `🔥 <b>${title}</b>\n\n${summary}\n\n📰 منبع: <a href="${link}">${source}</a>\n🏷️ ${hashtags}`;
    } else {
      text = `🤖 <b>${title}</b>\n\n${summary}\n\n📰 منبع: <a href="${link}">${source}</a>\n🏷️ ${hashtags}`;
    }

    if (item.confidence === 'rumor') {
      text += '\n\n⚠️ <i>این خبر تایید رسمی نشده است</i>';
    }

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
