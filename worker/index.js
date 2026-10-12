/**
 * Cloudflare Worker — ربات تلگرام همیشه فعال
 * دریافت webhook، دستورات، دکمه‌ها + انتشار در کانال
 */

const BOT_TOKEN = '';
const CHANNEL_ID = '';
const ADMIN_ID = 0;
const BASE = `https://api.telegram.org/bot${BOT_TOKEN}`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === '/webhook' && request.method === 'POST') {
      return handleWebhook(request, env);
    }

    if (path === '/run' && request.method === 'POST') {
      return handleRun(request, env);
    }

    return new Response('OK');
  }
};

async function handleWebhook(request, env) {
  const update = await request.json();

  if (update.callback_query) {
    await handleCallback(update.callback_query, env);
  } else if (update.message) {
    await handleMessage(update.message, env);
  }

  return new Response('OK');
}

async function handleCallback(cb, env) {
  const data = cb.data || '';
  const cbId = cb.id;
  const parts = data.split(':');
  const action = parts[0];
  const itemHash = parts[1] || '';

  const pending = await getJSON(env, 'pending', []);
  const approved = await getJSON(env, 'approved', []);

  if (action === 'approve') {
    const item = pending.find(i => i.hash === itemHash);
    if (item) {
      item.status = 'approved';
      item.admin_action = 'approved';
      approved.push(item);
      await saveJSON(env, 'pending', pending.filter(i => i.hash !== itemHash));
      await saveJSON(env, 'approved', approved);
      await answerCallback(cbId, '✅ تایید شد');
    }
  } else if (action === 'reject') {
    await saveJSON(env, 'pending', pending.filter(i => i.hash !== itemHash));
    await answerCallback(cbId, '❌ رد شد');
  } else if (action === 'feature') {
    const item = pending.find(i => i.hash === itemHash);
    if (item) {
      item.status = 'approved';
      item.admin_action = 'featured';
      approved.push(item);
      await saveJSON(env, 'pending', pending.filter(i => i.hash !== itemHash));
      await saveJSON(env, 'approved', approved);
      await answerCallback(cbId, '⭐️ ویژه');
    }
  } else if (action === 'edit') {
    const item = pending.find(i => i.hash === itemHash);
    if (item) {
      item._editing = true;
      await saveJSON(env, 'pending', pending);
      await sendMessage(ADMIN_ID, '✏️ متن ویرایش‌شده رو بفرست:');
      await answerCallback(cbId);
    }
  } else if (action === 'pause') {
    await updateState(env, { paused: true });
    await answerCallback(cbId, '⏸️ متوقف');
  } else if (action === 'resume') {
    await updateState(env, { paused: false });
    await answerCallback(cbId, '▶️ فعال');
  }
}

async function handleMessage(msg, env) {
  const text = (msg.text || '').trim();
  const chatId = msg.chat.id;

  if (chatId !== ADMIN_ID) return;

  const pending = await getJSON(env, 'pending', []);
  const approved = await getJSON(env, 'approved', []);
  const state = await getJSON(env, 'state', { paused: false, auto_publish: false, daily_post_count: 0, daily_post_date: null });

  // Check if editing
  for (const item of pending) {
    if (item._editing) {
      item.summary_fa = text;
      item._editing = false;
      item._edited = true;
      await saveJSON(env, 'pending', pending);
      await sendMessage(ADMIN_ID, '✅ ویرایش انجام شد');
      return;
    }
  }

  if (text === '/start') {
    await sendMessage(ADMIN_ID,
      '🤖 <b>ربات مدیریت کانال اخبار AI</b>\n\n' +
      'دستورات:\n' +
      '/status - وضعیت سیستم\n' +
      '/pending - اخبار در انتظار تایید\n' +
      '/pause - توقف انتشار\n' +
      '/resume - ادامه انتشار\n' +
      '/auto on|off - انتشار خودکار\n' +
      '/stats - آمار'
    );
  } else if (text === '/status') {
    const stage = state.current_stage || 'idle';
    const stageEmoji = { idle: '💤', collecting: '📡', analyzing: '🧠', publishing: '📤', waiting: '⏳' }[stage] || '❓';
    const paused = state.paused ? '⏸️ متوقف' : '▶️ فعال';
    const auto = state.auto_publish ? 'روشن' : 'خاموش';
    await sendMessage(ADMIN_ID,
      `📊 <b>وضعیت ربات</b>\n\n` +
      `${stageEmoji} مرحله: ${stage}\n` +
      `وضعیت: ${paused}\n` +
      `در انتظار تایید: ${pending.length}\n` +
      `آماده انتشار: ${approved.length}\n` +
      `انتشار خودکار: ${auto}`
    );
  } else if (text === '/pending') {
    if (pending.length === 0) {
      await sendMessage(ADMIN_ID, '📭 خبری در انتظار تایید نیست');
      return;
    }
    for (const item of pending.slice(0, 5)) {
      await sendReview(item);
    }
  } else if (text === '/pause') {
    await updateState(env, { paused: true });
    await sendMessage(ADMIN_ID, '⏸️ انتشار متوقف شد');
  } else if (text === '/resume') {
    await updateState(env, { paused: false });
    await sendMessage(ADMIN_ID, '▶️ انتشار ادامه یافت');
  } else if (text.startsWith('/auto')) {
    const parts = text.split();
    if (parts.length > 1 && ['on', 'off'].includes(parts[1])) {
      const val = parts[1] === 'on';
      await updateState(env, { auto_publish: val });
      await sendMessage(ADMIN_ID, `انتشار خودکار: ${val ? 'روشن ✅' : 'خاموش ❌'}`);
    }
  } else if (text === '/stats') {
    const published = await getJSON(env, 'published', []);
    const pubCount = published.filter(p => p.published).length;
    const rejCount = published.filter(p => !p.published).length;
    await sendMessage(ADMIN_ID,
      `📊 <b>آمار</b>\n\n` +
      `منتشر شده: ${pubCount}\n` +
      `رد شده: ${rejCount}`
    );
  }
}

async function sendReview(item) {
  const trust = item.source_trust || 3;
  const trustEmoji = trust >= 4 ? '🟢' : trust >= 3 ? '🟡' : '🔴';
  const score = item.score || 0;
  const scoreLabel = score >= 70 ? 'زیاد' : score >= 50 ? 'متوسط' : score >= 30 ? 'كم' : 'ضعیف';
  const scoreEmoji = score >= 70 ? '🟢' : score >= 50 ? '🟡' : score >= 30 ? '🟠' : '🔴';

  let text = `📰 <b>${item.title_fa}</b>\n\n`;
  text += `${item.summary_fa}\n\n`;
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
        { text: '✅ تایید', callback_data: `approve:${item.hash}` },
        { text: '❌ رد', callback_data: `reject:${item.hash}` }
      ],
      [
        { text: '⭐️ ویژه', callback_data: `feature:${item.hash}` },
        { text: '✏️ ویرایش', callback_data: `edit:${item.hash}` }
      ],
      [{ text: '🔗 مشاهده منبع', url: item.link }]
    ]
  };

  await sendMessage(ADMIN_ID, text, keyboard);
}

async function handleRun(request, env) {
  const approved = await getJSON(env, 'approved', []);
  const state = await getJSON(env, 'state', {});

  if (state.paused) {
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

    const result = await sendMessage(CHANNEL_ID, text);
    if (result && result.ok) {
      state.daily_post_count++;
      published++;
      const publishedLog = await getJSON(env, 'published', []);
      publishedLog.push({ ...item, published: true, published_at: new Date().toISOString() });
      await saveJSON(env, 'published', publishedLog);
    } else {
      remaining.push(item);
    }
  }

  await saveJSON(env, 'approved', remaining);
  await updateState(state, env);

  return new Response(JSON.stringify({ ok: true, published, remaining: remaining.length }), { headers: { 'Content-Type': 'application/json' } });
}

// === Helpers ===

async function sendMessage(chatId, text, replyMarkup = null) {
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

async function answerCallback(cbId, text) {
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
