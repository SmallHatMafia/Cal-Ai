<script>
  let command = '';
  let output = '';
  let ws;
  let lastOutput = '';
  let lastLogs = '';
  import { fetchLogs } from './cli';

  function connect() {
    ws = new WebSocket('ws://127.0.0.1:8000/ws/terminal');
    ws.onmessage = (event) => {
      // Split output and logs
      const text = event.data;
      const outputMatch = text.match(/\[OUTPUT\][\s\S]*?\[LOGS\]/);
      if (outputMatch) {
        const parts = text.split('[LOGS]');
        lastOutput = parts[0].replace('[OUTPUT]', '').trim();
        lastLogs = parts[1]?.trim() || '';
      } else {
        lastOutput = text;
        lastLogs = '';
      }
      output += `\n$ ${command}\n`;
      output += lastOutput ? lastOutput + '\n' : '';
      output += lastLogs ? '[logs]\n' + lastLogs + '\n' : '';
      // Automatically fetch logs after each command
      getLogs();
    };
    ws.onclose = () => {
      output += '\n[connection closed]\n';
    };
  }

  function sendCommand() {
    if (!ws || ws.readyState !== 1) connect();
    ws.onopen = () => ws.send(command);
    if (ws.readyState === 1) ws.send(command);
    command = '';
  }

  async function getLogs() {
    const res = await fetchLogs();
    if (res.logs) {
      output += '\n[logs from /logs endpoint]\n' + res.logs + '\n';
    } else {
      output += '\n[logs from /logs endpoint]\n(No logs)\n';
    }
  }
</script>

<div style="font-family: monospace; background: #111; color: #0f0; padding: 1em; border-radius: 8px;">
  <!-- Debug Terminal: Direct shell commands via WebSocket -->
  <h3>Debug Terminal</h3>
  <div style="min-height: 200px; white-space: pre-wrap;">{output}</div>
  <input
    bind:value={command}
    on:keydown={(e) => e.key === 'Enter' && sendCommand()}
    placeholder="Type a command and press Enter"
    style="width: 80%; background: #222; color: #0f0; border: none; padding: 0.5em; margin-top: 1em;"
  />
  <button on:click={sendCommand} style="background: #333; color: #0f0; border: none; padding: 0.5em 1em; margin-left: 1em;">Send</button>
</div>

<style>
input:focus {
  outline: 1px solid #0f0;
}
</style> 