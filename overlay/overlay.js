const rail = document.getElementById('tsrail-rail');
const POLL_MS = 400;

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

function buildUser(user) {
  const wrapper = document.createElement('div');
  wrapper.className = 'tsrail-user ' + (user.talking ? 'talking' : 'idle');
  wrapper.dataset.uid = user.uid;

  const frame = document.createElement('div');
  frame.className = 'tsrail-frame';
  const frameIdle = document.createElement('img');
  frameIdle.className = 'frame frame-idle';
  frameIdle.src = user.assets.frame_idle;
  frame.appendChild(frameIdle);
  if (user.assets.frame_talk) {
    const frameTalk = document.createElement('img');
    frameTalk.className = 'frame frame-talk';
    frameTalk.src = user.assets.frame_talk;
    frame.appendChild(frameTalk);
  }

  const avatar = document.createElement('div');
  avatar.className = 'tsrail-avatar';
  const avatarIdle = document.createElement('img');
  avatarIdle.className = 'avatar avatar-idle';
  avatarIdle.src = user.assets.avatar_idle;
  avatar.appendChild(avatarIdle);
  const talkSrc = user.assets.avatar_talk || user.assets.avatar_idle;
  if (talkSrc) {
    const avatarTalk = document.createElement('img');
    avatarTalk.className = 'avatar avatar-talk';
    avatarTalk.src = talkSrc;
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

function render(state) {
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
  if (state) render(state);
  setTimeout(loop, POLL_MS);
}

loop();
