export function encodeYjsUpdate(update: Uint8Array): string {
  let binary = '';
  const chunkSize = 0x8000;

  for (let index = 0; index < update.length; index += chunkSize) {
    const chunk = update.subarray(index, index + chunkSize);
    binary += String.fromCharCode(...chunk);
  }

  return btoa(binary);
}

export function decodeYjsUpdate(encoded: string): Uint8Array {
  const binary = atob(encoded);
  const update = new Uint8Array(binary.length);

  for (let index = 0; index < binary.length; index += 1) {
    update[index] = binary.charCodeAt(index);
  }

  return update;
}
