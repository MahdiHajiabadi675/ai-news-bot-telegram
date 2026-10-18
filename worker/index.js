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

      const update = await request.json();

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
  }
};

async function handleCallback(cb, env, BASE, CHANNEL_ID, ADMIN_ID) {
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
    }
  } else if (action === 'reject') {
    await saveJSON(env, 'pending', pending.filter(i => itemKey(i) !== itemHash));
    await answerCallback(BASE, cbId, '❌ رد شد');
  } else if (action === 'feature') {
    const idx = pending.findIndex(i => itemKey(i) === itemHash);
    if (idx !== -1) {
      const item = pending.splice(idx, 1)[0];
      item.status = 'approved';
      item.admin_action = 'featured';
      approved.push(item);
      await saveJSON(env, 'pending', pending);
      await saveJSON(env, 'approved', approved);
      await answerCallback(BASE, cbId, '⭐️ ویژه');
    }
  } else if (action === 'edit') {
    const item = pending.find(i => itemKey(i) === itemHash);
    if (item) {
      item._editing = true;
      await saveJSON(env, 'pending', pending);
      await sendMessage(BASE, ADMIN_ID, '✏️ متن ویرایش‌شده رو بفرست:');
      await answerCallback(BASE, cbId, '');
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

  // Check if editing
  for (const item of pending) {
    if (item._editing) {
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
    const maxPosts = 15;

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

    await sendMessage(BASE, ADMIN_ID,
      `📊 <b>وضعیت ربات</b>\n\n` +
      `${stageEmoji} مرحله فعلی: <b>${stage}</b>\n` +
      `━━━━━━━━━━━━━━\n` +
      `وضعیت: ${paused}\n` +
      `انتشار خودکار: ${auto}\n` +
      `━━━━━━━━━━━━━━\n` +
      `📰 امروز: ${dailyCount}/${maxPosts}\n` +
      `📋 در انتظار تایید: ${pending.length}\n` +
      `✅ آماده انتشار: ${approved.length}\n` +
      `━━━━━━━━━━━━━━\n` +
      `📈 کل منتشر شده: ${totalPublished}\n` +
      `❌ کل رد شده: ${totalRejected}\n` +
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
      await updateState(env, { auto_publish_enabled: val });
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
  let text = `📰 <b>${item.title_fa}</b>\n\n`;
  text += `${item.summary_fa}\n\n`;
  text += `🆔 ID: <code>${id || 'N/A'}</code>\n`;
  text += `📌 اهمیت: ${item.importance} | اعتبار: ${item.confidence}\n`;
  text += `${trustEmoji} منبع: ${item.source} (${trust}/5)\n`;
  text += `${scoreEmoji} اسکور: ${score}/100 (${scoreLabel})\n`;
  text += `🏷️ ${(item.hashtags || []).map(h => '#' + h).join(' ')}\n`;
  text += `🔗 ${item.link}`;
  if (item.admin_question) {
    text += `\n\n❓ ${item.admin_question}`;
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
      [{ text: '🔗 مشاهده منبع', url: item.link }]
    ]
  };

  await sendMessage(BASE, ADMIN_ID, text, keyboard);
}

async function publishItem(env, BASE, CHANNEL_ID, ADMIN_ID, item) {
  const state = await getJSON(env, 'state', {});
  if (state.paused) {
    console.log('[publishItem] Bot is paused');
    return;
  }

  const today = new Date().toISOString().slice(0, 10);
  if (state.daily_post_date !== today) {
    state.daily_post_date = today;
    state.daily_post_count = 0;
  }

  if (state.daily_post_count >= 15) {
    console.log('[publishItem] Daily limit reached');
    return;
  }

  let text = '';
  if (item.category === 'fast_track') {
    text = `📡 <b>${item.title_fa}</b>\n\n${item.summary_fa}\n\n📰 منبع: <a href="${item.link}">${item.source}</a>\n🏷️ ${(item.hashtags || []).map(h => '#' + h).join(' ')}`;
  } else if (item.admin_action === 'featured') {
    text = `⭐️ <b>${item.title_fa}</b>\n\n${item.summary_fa}\n\n📰 منبع: <a href="${item.link}">${item.source}</a>\n🏷️ ${(item.hashtags || []).map(h => '#' + h).join(' ')}`;
  } else if (item.importance === 'high') {
    text = `🔥 <b>${item.title_fa}</b>\n\n${item.summary_fa}\n\n📰 منبع: <a href="${item.link}">${item.source}</a>\n🏷️ ${(item.hashtags || []).map(h => '#' + h).join(' ')}`;
  } else {
    text = `🤖 <b>${item.title_fa}</b>\n\n${item.summary_fa}\n\n📰 منبع: <a href="${item.link}">${item.source}</a>\n🏷️ ${(item.hashtags || []).map(h => '#' + h).join(' ')}`;
  }

  if (item.confidence === 'rumor') {
    text += '\n\n⚠️ <i>این خبر تایید رسمی نشده است</i>';
  }

  const publishedLog = await getJSON(env, 'published', []);
  const key = itemKey(item);
  if (key && publishedLog.some(p => p.published && itemKey(p) === key)) {
    console.log('[publishItem] Already published, skipping');
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

  const MAX_POSTS = 15;
  let published = 0;
  const remaining = [];
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
      remaining.push(item);
      continue;
    }

    let text = '';
    if (item.category === 'fast_track') {
      text = `📡 <b>${item.title_fa}</b>\n\n${item.summary_fa}\n\n📰 منبع: <a href="${item.link}">${item.source}</a>\n🏷️ ${(item.hashtags || []).map(h => '#' + h).join(' ')}`;
    } else if (item.admin_action === 'featured') {
      text = `⭐️ <b>${item.title_fa}</b>\n\n${item.summary_fa}\n\n📰 منبع: <a href="${item.link}">${item.source}</a>\n🏷️ ${(item.hashtags || []).map(h => '#' + h).join(' ')}`;
    } else if (item.importance === 'high') {
      text = `🔥 <b>${item.title_fa}</b>\n\n${item.summary_fa}\n\n📰 منبع: <a href="${item.link}">${item.source}</a>\n🏷️ ${(item.hashtags || []).map(h => '#' + h).join(' ')}`;
    } else {
      text = `🤖 <b>${item.title_fa}</b>\n\n${item.summary_fa}\n\n📰 منبع: <a href="${item.link}">${item.source}</a>\n🏷️ ${(item.hashtags || []).map(h => '#' + h).join(' ')}`;
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
      remaining.push(item);
    }
  }

  await saveJSON(env, 'approved', remaining);
  await saveJSON(env, 'state', state);
  if (published > 0) await saveJSON(env, 'published', publishedLog);

  return new Response(JSON.stringify({ ok: true, published, remaining: remaining.length }), { headers: { 'Content-Type': 'application/json' } });
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

async function answerCallback(BASE, cbId, text) {
  try {
    await fetch(`${BASE}/answerCallbackQuery`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ callback_query_id: cbId, text })
    });
  } catch (e) {
    console.error('answerCallback error:', e);
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
  Object.assign(state, updates);
  await saveJSON(env, 'state', state);
}

/** کلید یکتای خبر — بعضی آیتم‌های قدیمی «hash» دارن، جدیدها «id» */
function itemKey(item) {
  return item.id || item.hash || '';
}
