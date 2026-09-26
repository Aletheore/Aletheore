export async function refreshAfterFonts(
  fonts: { ready: Promise<unknown> } | undefined,
  refresh: () => void,
): Promise<void> {
  if (!fonts) {
    refresh();
    return;
  }
  try {
    await fonts.ready;
  } finally {
    refresh();
  }
}
