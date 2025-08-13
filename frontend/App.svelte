<script>
  import MainTerminal from './MainTerminal.svelte';
  import Terminal from './Terminal.svelte';
  let showMain = true;
</script>

<div style="display: flex; flex-direction: column; align-items: center; margin-top: 2em;">
  <div style="margin-bottom: 1em; display:flex; gap: 0.5em; align-items:center;">
    <button on:click={() => showMain = true} style="padding: 0.5em 1.5em; margin-right: 1em; background: {showMain ? 'black' : '#222'}; color: white; border: none; border-radius: 5px; font-weight: bold;">Main</button>
    <button on:click={() => showMain = false} style="padding: 0.5em 1.5em; background: {!showMain ? 'black' : '#222'}; color: white; border: none; border-radius: 5px; font-weight: bold;">Debug</button>
    <span style="color:#888; font-size: 0.9em;">Upload image and click Start to run pipeline</span>
  </div>
  <div style="width: 100%; max-width: 600px;">
    <div style="display: {showMain ? 'block' : 'none'};">
      <MainTerminal />
    </div>
    <div style="display: {!showMain ? 'block' : 'none'};">
      <Terminal />
    </div>
  </div>
</div> 