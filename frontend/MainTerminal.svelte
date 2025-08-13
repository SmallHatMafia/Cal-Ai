<script>
  import { fetchTest, sendApiCommand } from './cli';
  let command = '';
  let output = '';
  let selectedFile = null;
  let busy = false;
  let lastPipeline = null;

  async function startPipeline() {
    if (!selectedFile) {
      output += '[ERROR]\nNo image selected.\n';
      return;
    }
    busy = true;
    try {
      // Step 1: visual-context
      const form = new FormData();
      form.append('file', selectedFile);
      const vcRes = await fetch('/api/bots/visual-context', { method: 'POST', body: form });
      const visualJson = await vcRes.json();
      if (!vcRes.ok) throw new Error(visualJson?.error || 'visual-context failed');

      // Step 2: dish-determiner
      const ddRes = await fetch('/api/bots/dish-determiner', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ visual_json: visualJson, image_token: visualJson._image_token })
      });
      const dishJson = await ddRes.json();
      if (!ddRes.ok) throw new Error(dishJson?.error || 'dish-determiner failed');

      // Step 3: restaurant-calories if RESTAURANT
      if (dishJson.source === 'RESTAURANT') {
        const rcRes = await fetch('/api/bots/restaurant-calories', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ visual_json: visualJson, dish_json: dishJson, image_token: visualJson._image_token })
        });
        const calJson = await rcRes.json();
        if (!rcRes.ok) throw new Error(calJson?.error || 'restaurant-calories failed');

        const brand = calJson.itemized?.restaurant_name || calJson.macros?.restaurant_name || dishJson.restaurant_name || null;
        // LLM itemizer output (before Nutritionix)
        const itemizerItems = (calJson.itemized?.items || []).map(i => ({
          item_name: i.item_name,
          description: i.description,
          quantity: i.quantity ?? 1,
          size: i.size,
          portion_detail: i.portion_detail,
          nutritionix_query: i.nutritionix_query
        }));
        const items = (calJson.macros?.results || []).map(r => ({
          food_name: (r.macros?.food_name) ?? (r.nutritionix_match?.food_name) ?? r.item_name,
          brand_name: (r.macros?.brand_name) ?? (r.nutritionix_match?.brand_name) ?? brand,
          quantity: r.quantity ?? 1,
          macros: r.macros
        }));
        const total = {
          source: dishJson.source,
          dish_name: dishJson.dish_name,
          restaurant_name: brand,
          steps_sec: {
            visual_context: (visualJson._duration_ms || 0) / 1000,
            dish_determiner: (dishJson._duration_ms || 0) / 1000,
            restaurant_itemizer: (calJson.itemized?._duration_ms || 0) / 1000,
            nutritionix_lookup: (calJson.macros?._duration_ms || 0) / 1000
          },
          total_sec: (calJson._total_duration_ms || 0) / 1000,
           // Show LLM itemizer results first, including quantities
          itemizer_items: itemizerItems,
          items
        };
        output += JSON.stringify(total, null, 2) + '\n';
        lastPipeline = total;
      } else {
        // HOME-COOKED: run home-cooked analyzer
        const hcRes = await fetch('/api/bots/home-cooked', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dish_json: dishJson, image_token: visualJson._image_token })
        });
        const homeJson = await hcRes.json();
        if (!hcRes.ok) throw new Error(homeJson?.error || 'home-cooked failed');
        const total = {
          source: dishJson.source,
          dish_name: dishJson.dish_name,
          home_cooked: homeJson,
          steps_sec: {
            visual_context: (visualJson._duration_ms || 0) / 1000,
            dish_determiner: (dishJson._duration_ms || 0) / 1000,
            home_cooked: (homeJson._duration_ms || 0) / 1000,
          }
        };
        lastPipeline = total;
        output += JSON.stringify(total, null, 2) + '\n';
      }
    } catch (e) {
      output += '[ERROR]\n' + (e?.message || e) + '\n';
    } finally {
      busy = false;
    }
  }

  function parseInput(input) {
    // Split by space, but keep quoted strings together
    const regex = /(?:"([^"]*)")|(\S+)/g;
    const args = [];
    let match;
    while ((match = regex.exec(input)) !== null) {
      if (match[1] !== undefined) {
        args.push(match[1]);
      } else if (match[2] !== undefined) {
        args.push(match[2]);
      }
    }
    if (args.length === 0) return { command: '', args: [] };
    return { command: args[0], args: args.slice(1) };
  }

  async function sendCommand() {
    output += `\n$ ${command}\n`;
    const { command: cmd, args } = parseInput(command.trim());
    if (!cmd) {
      output += 'No command entered.\n';
      command = '';
      return;
    }
    if (cmd === 'fetch' && args[0] === 'test') {
      const res = await fetchTest();
      if (res.result) {
        output += '[RESULT]\n' + JSON.stringify(res.result, null, 2) + '\n';
      }
      if (res.logs) {
        output += '[LOGS]\n' + res.logs + '\n';
      }
    } else {
      // Use generic API command sender
      try {
        const res = await sendApiCommand(cmd, args);
        output += '[API RESULT]\n' + JSON.stringify(res, null, 2) + '\n';
      } catch (e) {
        output += '[ERROR]\n' + e + '\n';
      }
    }
    command = '';
  }
</script>

<div style="font-family: monospace; background: #111; color: #0ff; padding: 1em; border-radius: 8px;">
  <!-- Main Terminal: Handles API commands and pipeline run -->
  <h3>Main Terminal</h3>
  <div style="margin-bottom: 0.75em; display:flex; gap:0.5em; align-items:center;">
    <input type="file" accept="image/*" on:change={(e) => selectedFile = e.target.files?.[0] || null} style="color:#0ff;" />
    <button on:click={startPipeline} disabled={busy} style="background: #095; color: #fff; border: none; padding: 0.5em 1em;">{busy ? 'Running…' : 'Start'}</button>
  </div>
  <div style="min-height: 200px; white-space: pre-wrap;">{output}</div>
  <input
    bind:value={command}
    on:keydown={(e) => e.key === 'Enter' && sendCommand()}
    placeholder="Type an API command (e.g., return chicken false) and press Enter"
    style="width: 80%; background: #222; color: #0ff; border: none; padding: 0.5em; margin-top: 1em;"
  />
  <button on:click={sendCommand} style="background: #333; color: #0ff; border: none; padding: 0.5em 1em; margin-left: 1em;">Send</button>
</div>

<style>
input:focus {
  outline: 1px solid #0ff;
}
</style> 