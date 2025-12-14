const rail = document.getElementById('tsrail-rail');
const errorBanner = document.getElementById('tsrail-error');
const POLL_MS = 400;
let lastState = null;

async function fetchState() {
  try {
    const res = await fetch('/state.json', { cache: 'no-cache' });
    if (!res.ok) throw new Error('http ' + res.status);
    return await res.json();
  } catch (err) {
    console.warn('state fetch failed', err);
    return null;
  }
}

function setErrorVisible(show) {
  if (!errorBanner) return;
  errorBanner.classList.toggle('hidden', !show);
}

function assetUrl(path) {
  if (!path) return null;
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  if (path.startsWith('/')) return path;
  return '/' + path.replace(/^\/+/, '');
}

const FALLBACK_FRAME_IDLE = assetUrl('assets/frames/monitor_idle.svg');
const FALLBACK_FRAME_TALK = assetUrl('assets/frames/monitor_talk.svg');

function applyFrameFallback(img, fallbackSrc) {
  if (!img) return;
  img.onerror = () => {
    if (img.src === fallbackSrc) return;
    img.onerror = null;
    img.src = fallbackSrc;
  };
}

function buildUser(user) {
  const wrapper = document.createElement('div');
  wrapper.className = 'tsrail-user ' + (user.talking ? 'talking' : 'idle');
  wrapper.dataset.uid = user.uid;

  const frame = document.createElement('div');
  frame.className = 'tsrail-frame';
  const frameIdle = document.createElement('img');
  frameIdle.className = 'frame frame-idle';
  frameIdle.src = assetUrl(user.assets.frame_idle) || FALLBACK_FRAME_IDLE;
  applyFrameFallback(frameIdle, FALLBACK_FRAME_IDLE);
  frame.appendChild(frameIdle);
  const frameTalk = document.createElement('img');
  frameTalk.className = 'frame frame-talk';
  frameTalk.src = assetUrl(user.assets.frame_talk) || FALLBACK_FRAME_TALK;
  applyFrameFallback(frameTalk, FALLBACK_FRAME_TALK);
  frame.appendChild(frameTalk);

  const avatar = document.createElement('div');
  avatar.className = 'tsrail-avatar';
  const avatarIdle = document.createElement('img');
  avatarIdle.className = 'avatar avatar-idle';
  avatarIdle.src = assetUrl(user.assets.avatar_idle);
  avatar.appendChild(avatarIdle);
  const talkSrc = user.assets.avatar_talk || user.assets.avatar_idle;
  if (talkSrc) {
    const avatarTalk = document.createElement('img');
    avatarTalk.className = 'avatar avatar-talk';
    avatarTalk.src = assetUrl(talkSrc);
    avatar.appendChild(avatarTalk);
  }

  const nick = document.createElement('div');
  nick.className = 'tsrail-nickname';
  nick.textContent = user.nickname;

  wrapper.appendChild(frame);
  wrapper.appendChild(avatar);
  wrapper.appendChild(nick);
  return wrapper;
}

function renderRail(state) {
  rail.innerHTML = '';
  const users = state?.users || [];
  if (!users.length) {
    const empty = document.createElement('div');
    empty.className = 'tsrail-empty';
    empty.textContent = 'Waiting for approved users...';
    rail.appendChild(empty);
    return;
  }
  users.forEach((user) => {
    rail.appendChild(buildUser(user));
  });
}

async function loop() {
  const state = await fetchState();
  if (state) {
    lastState = state;
    renderRail(state);
    setErrorVisible(false);
  } else {
    // Keep the last known view (if any) and surface a lightweight error.
    setErrorVisible(true);
  }
  setTimeout(loop, POLL_MS);
}

loop();
