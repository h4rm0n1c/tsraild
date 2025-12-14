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

function buildUser(user) {
  const wrapper = document.createElement('div');
  wrapper.className = 'tsrail-user ' + (user.talking ? 'talking' : 'idle');
  wrapper.dataset.uid = user.uid;

  const frame = document.createElement('div');
  frame.className = 'tsrail-frame';
  const frameIdle = document.createElement('img');
  frameIdle.className = 'frame frame-idle';
  frameIdle.src = assetUrl(user.assets.frame_idle);
  frame.appendChild(frameIdle);
  if (user.assets.frame_talk) {
    const frameTalk = document.createElement('img');
    frameTalk.className = 'frame frame-talk';
    frameTalk.src = assetUrl(user.assets.frame_talk);
    frame.appendChild(frameTalk);
  }

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

  wrapper.appendChild(avatar);
  wrapper.appendChild(frame);
  wrapper.appendChild(nick);
  return wrapper;
}

function applyTalkingState(node, talking) {
  const target = talking ? 'talking' : 'idle';
  const isTalking = node.classList.contains('talking');
  if (isTalking === talking) return;

  node.classList.remove('talking', 'idle');
  // Force a reflow so the hop animation plays once when toggling state.
  void node.offsetWidth;
  node.classList.add(target);
}

function syncAssets(node, user) {
  const frameIdle = node.querySelector('.frame-idle');
  const frameTalk = node.querySelector('.frame-talk');
  const avatarIdle = node.querySelector('.avatar-idle');
  const avatarTalk = node.querySelector('.avatar-talk');

  if (frameIdle) frameIdle.src = assetUrl(user.assets.frame_idle);
  if (frameTalk && user.assets.frame_talk) frameTalk.src = assetUrl(user.assets.frame_talk);
  if (avatarIdle) avatarIdle.src = assetUrl(user.assets.avatar_idle);

  const talkSrc = user.assets.avatar_talk || user.assets.avatar_idle;
  if (avatarTalk && talkSrc) avatarTalk.src = assetUrl(talkSrc);
}

function renderRail(state) {
  const users = state?.users || [];
  const existing = new Map(
    Array.from(rail.querySelectorAll('.tsrail-user')).map((node) => [node.dataset.uid, node])
  );

  const empty = rail.querySelector('.tsrail-empty');
  if (!users.length) {
    existing.forEach((node) => node.remove());
    if (!empty) {
      const placeholder = document.createElement('div');
      placeholder.className = 'tsrail-empty';
      placeholder.textContent = 'Waiting for approved users...';
      rail.appendChild(placeholder);
    }
    return;
  }

  if (empty) empty.remove();

  users.forEach((user, index) => {
    let node = existing.get(user.uid);
    if (!node) {
      node = buildUser(user);
    } else {
      applyTalkingState(node, user.talking);
      syncAssets(node, user);
      const nick = node.querySelector('.tsrail-nickname');
      if (nick && nick.textContent !== user.nickname) {
        nick.textContent = user.nickname;
      }
    }

    node.style.order = index;
    if (node.parentElement !== rail) {
      rail.appendChild(node);
    }

    existing.delete(user.uid);
  });

  existing.forEach((node) => node.remove());
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
