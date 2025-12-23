const rail = document.getElementById('tsrail-rail');
const errorBanner = document.getElementById('tsrail-error');
const POLL_MS = 400;
let lastState = null;

const userElements = new Map();
let emptyMessage = null;

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

const FALLBACK_FRAME_IDLE = assetUrl('assets/frames/tv_idle.png');
const FALLBACK_FRAME_TALK = assetUrl('assets/frames/tv_talk.png');
const FALLBACK_FRAME_MASK = assetUrl('assets/frames/tv_mask.png');

function applyFrameFallback(img, fallbackSrc) {
  if (!img) return;
  img.onerror = () => {
    if (img.src === fallbackSrc) return;
    img.onerror = null;
    img.src = fallbackSrc;
  };
}

function applyTalkingState(userEl, talking) {
  const wasTalking = userEl.classList.contains('talking');
  if (talking === wasTalking) return;

  // Force a reflow only on change to let hop animations play once per transition.
  void userEl.offsetHeight;
  userEl.classList.toggle('talking', talking);
  userEl.classList.toggle('idle', !talking);
}

function ensureFrameAssets(wrapper, assets) {
  const frame = wrapper.querySelector('.tsrail-frame') || document.createElement('div');
  frame.className = 'tsrail-frame';

  let frameMask = frame.querySelector('.frame-mask');
  if (!frameMask) {
    frameMask = document.createElement('img');
    frameMask.className = 'frame frame-mask';
    frame.appendChild(frameMask);
  }
  frameMask.src = FALLBACK_FRAME_MASK;

  let frameIdle = frame.querySelector('.frame-idle');
  if (!frameIdle) {
    frameIdle = document.createElement('img');
    frameIdle.className = 'frame frame-idle';
    frame.appendChild(frameIdle);
  }
  frameIdle.src = assetUrl(assets.frame_idle) || FALLBACK_FRAME_IDLE;
  applyFrameFallback(frameIdle, FALLBACK_FRAME_IDLE);

  let frameTalk = frame.querySelector('.frame-talk');
  if (!frameTalk) {
    frameTalk = document.createElement('img');
    frameTalk.className = 'frame frame-talk';
    frame.appendChild(frameTalk);
  }
  frameTalk.src = assetUrl(assets.frame_talk) || FALLBACK_FRAME_TALK;
  applyFrameFallback(frameTalk, FALLBACK_FRAME_TALK);

  if (!frame.parentElement) {
    wrapper.appendChild(frame);
  }
}

function ensureAvatarAssets(wrapper, assets) {
  const avatar = wrapper.querySelector('.tsrail-avatar') || document.createElement('div');
  avatar.className = 'tsrail-avatar';

  let avatarIdle = avatar.querySelector('.avatar-idle');
  if (!avatarIdle) {
    avatarIdle = document.createElement('img');
    avatarIdle.className = 'avatar avatar-idle';
    avatar.appendChild(avatarIdle);
  }
  avatarIdle.src = assetUrl(assets.avatar_idle) || '';

  let avatarUnderlay = avatar.querySelector('.avatar-underlay');
  if (!avatarUnderlay) {
    avatarUnderlay = document.createElement('img');
    avatarUnderlay.className = 'avatar avatar-underlay';
    avatar.appendChild(avatarUnderlay);
  }
  avatarUnderlay.src = assetUrl(assets.avatar_idle) || '';

  const talkSrc = assets.avatar_talk || assets.avatar_idle;
  const talkUrl = talkSrc ? assetUrl(talkSrc) : null;
  let avatarTalk = avatar.querySelector('.avatar-talk');
  if (talkUrl) {
    if (!avatarTalk) {
      avatarTalk = document.createElement('img');
      avatarTalk.className = 'avatar avatar-talk';
      avatar.appendChild(avatarTalk);
    }
    avatarTalk.src = talkUrl;
  } else if (avatarTalk) {
    avatarTalk.remove();
  }

  if (!avatar.parentElement) {
    wrapper.appendChild(avatar);
  }
}

function ensureNickname(wrapper, nickname) {
  let nick = wrapper.querySelector('.tsrail-nickname');
  if (!nick) {
    nick = document.createElement('div');
    nick.className = 'tsrail-nickname';
    wrapper.appendChild(nick);
  }
  if (nick.textContent !== nickname) {
    nick.textContent = nickname;
  }
}

function buildOrUpdateUser(user) {
  let wrapper = userElements.get(user.uid);
  if (!wrapper) {
    wrapper = document.createElement('div');
    wrapper.dataset.uid = user.uid;
    userElements.set(user.uid, wrapper);
  }

  wrapper.classList.add('tsrail-user');
  applyTalkingState(wrapper, !!user.talking);
  ensureFrameAssets(wrapper, user.assets || {});
  ensureAvatarAssets(wrapper, user.assets || {});
  ensureNickname(wrapper, user.nickname);
  return wrapper;
}

function renderRail(state) {
  const users = state?.users || [];

  // Remove any placeholder message when real users arrive.
  if (users.length && emptyMessage) {
    emptyMessage.remove();
    emptyMessage = null;
  }

  if (!users.length) {
    if (!emptyMessage) {
      emptyMessage = document.createElement('div');
      emptyMessage.className = 'tsrail-empty';
      emptyMessage.textContent = 'Waiting for approved users...';
    }
    // Clear stale user nodes while keeping the placeholder intact.
    rail.querySelectorAll('.tsrail-user').forEach((el) => el.remove());
    userElements.clear();
    if (!emptyMessage.parentElement) rail.appendChild(emptyMessage);
    return;
  }

  const seen = new Set();
  const orderedNodes = users.map((user) => {
    const node = buildOrUpdateUser(user);
    seen.add(user.uid);
    return node;
  });

  // Remove nodes that are no longer present.
  userElements.forEach((node, uid) => {
    if (!seen.has(uid)) {
      node.remove();
      userElements.delete(uid);
    }
  });

  // Reorder without rebuilding the rail to keep animations stable.
  let cursor = rail.firstElementChild;
  orderedNodes.forEach((node) => {
    if (node === cursor) {
      cursor = cursor.nextElementSibling;
      return;
    }
    rail.insertBefore(node, cursor);
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
