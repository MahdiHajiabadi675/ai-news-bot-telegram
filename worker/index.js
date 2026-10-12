/**
 * Cloudflare Worker — ربات تلگرام همیشه فعال
 * مقادیر از Secrets خونده میشن
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
      const update = await request.json();

      if (update.callback_query) {
        await handleCallback(update.callback_query, env, BASE, CHANNEL_ID, ADMIN_ID);
      } else if (update.message) {
        await handleMessage(update.message, env, BASE, CHANNEL_ID, ADMIN_ID);
      }

      return new Response('OK');
    }

    if (path === '/run' && request.method === 'POST') {
      return handleRun(env, BASE, CHANNEL_ID, ADMIN_ID);
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
    const idx = pending.findIndex(i => i.hash === itemHash);
    if (idx !== -1) {
      const item = pending.splice(idx, 1)[0];
      item.status = 'approved';
      item.admin_action = 'approved';
      approved.push(item);
      await saveJSON(env, 'pending', pending);
      await saveJSON(env, 'approved', approved);
      await answerCallback(BASE, cbId, '✅ تایید شد');
    }
  } else if (action === 'reject') {
    await saveJSON(env, 'pending', pending.filter(i => i.hash !== itemHash));
    await answerCallback(BASE, cbId, '❌ رد شد');
  } else if (action === 'feature') {
    const idx = pending.findIndex(i => i.hash === itemHash);
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
    const item = pending.find(i => i.hash === itemHash);
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
  const state = await getJSON(env, 'state', { paused: false, auto_publish: false, daily_post_count: 0, daily_post_date: null });

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
      '/stats - آمار'
    );
  } else if (text === '/status') {
    const stage = state.current_stage || 'idle';
    const stageEmoji = { idle: '💤', collecting: '📡', analyzing: '🧠', publishing: '📤', waiting: '⏳' }[stage] || '❓';
    const paused = state.paused ? '⏸️ متوقف' : '▶️ فعال';
    const auto = state.auto_publish ? 'روشن' : 'خاموش';
    await sendMessage(BASE, ADMIN_ID,
      `📊 <b>وضعیت ربات</b>\n\n` +
      `${stageEmoji} مرحله: ${stage}\n` +
      `وضعیت: ${paused}\n` +
      `در انتظار تایید: ${pending.length}\n` +
      `آماده انتشار: ${approved.length}\n` +
      `انتشار خودکار: ${auto}`
    );
  } else if (text === '/pending') {
    if (pending.length === 0) {
      await sendMessage(BASE, ADMIN_ID, '📭 خبری در انتظار تایید نیست');
      return;
    }
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
    const parts = text.split();
    if (parts.length > 1 && ['on', 'off'].includes(parts[1])) {
      const val = parts[1] === 'on';
      await updateState(env, { auto_publish: val });
      await sendMessage(BASE, ADMIN_ID, `انتشار خودکار: ${val ? 'روشن ✅' : 'خاموش ❌'}`);
    }
  } else if (text === '/stats') {
    const published = await getJSON(env, 'published', []);
    const pubCount = published.filter(p => p.published).length;
    const rejCount = published.filter(p => !p.published).length;
    await sendMessage(BASE, ADMIN_ID,
      `📊 <b>آمار</b>\n\n` +
      `منتشر شده: ${pubCount}\n` +
      `رد شده: ${rejCount}`
    );
  }
}

async function sendReview(BASE, ADMIN_ID, item) {
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

  await sendMessage(BASE, ADMIN_ID, text, keyboard);
}

async function handleRun(env, BASE, CHANNEL_ID, ADMIN_ID) {
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

    const result = await sendMessage(BASE, CHANNEL_ID, text);
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
  await saveJSON(env, 'state', state);

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
