// One artwork source for both the Agent library and the active contact card.
const knownAgents = {
  'f6863732-e5fe-4a20-9caf-a9983c4d60f2': 'sweet-maid',
  '9a09fe60-f6ec-4135-a1d6-4b9e17940584': 'ahui',
  '693a3f86-eb33-4254-937c-cd642579e8ef': 'hermes',
  '2942b564-0c08-4d3d-b1ee-b160bbd55194': 'vibe-trading',
};
const artworkVersions = {'sweet-maid': 1, ahui: 3, hermes: 2, 'vibe-trading': 2};

export function artworkFor(agent) {
  if (!agent) return null;
  const key = knownAgents[agent.id]
    || (agent.kind === 'hermes' ? 'hermes' : agent.kind === 'vibe' ? 'vibe-trading' : '')
    || (/甜心女仆/.test(agent.name) ? 'sweet-maid' : /阿慧/.test(agent.name) ? 'ahui' : '');
  return key ? {key, src: `/assets/agent-art/${key}-v${artworkVersions[key]}.png`} : null;
}

export function artworkHTML(art) {
  // The path comes exclusively from the local artwork allowlist above.
  return art ? `<img class="agent-artwork" src="${art.src}" alt="" aria-hidden="true" loading="lazy" decoding="async"><span class="agent-art-wash" aria-hidden="true"></span>` : '';
}

export function syncContactArtwork(card, agent) {
  if (!card) return;
  const art = artworkFor(agent);
  const key = art?.key || '';
  if (card.dataset.agentArt === key && (!art || card.querySelector('.agent-artwork')?.getAttribute('src') === art.src)) return;
  card.querySelectorAll(':scope > .agent-artwork, :scope > .agent-art-wash').forEach(node => node.remove());
  card.dataset.agentArt = key;
  card.classList.toggle('has-agent-art', !!art);
  if (art) card.insertAdjacentHTML('afterbegin', artworkHTML(art));
}
