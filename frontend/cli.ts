export async function fetchTest() {
  const response = await fetch('/api/cli', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command: 'fetch', args: ['test'] })
  });
  return response.json();
}

export async function fetchLogs() {
  const response = await fetch('/api/logs');
  return response.json();
}

// Generic API command sender
export async function sendApiCommand(command: string, args: string[]) {
  const response = await fetch('/api/cli', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command, args })
  });
  return response.json();
}
