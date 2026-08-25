export const CANVAS_REPLACED_CLOSE_CODE = 4001;
export const CANVAS_SAME_OWNER_REPLACED_CLOSE_CODE = 4002;
export const CANVAS_INVALID_CONNECTION_CLOSE_CODE = 4400;
export const CANVAS_AUTH_REQUIRED_CLOSE_CODE = 4401;
export const CANVAS_NOT_FOUND_CLOSE_CODE = 4404;

export const CANVAS_SAME_OWNER_MAX_RETRIES = 3;
export const CANVAS_RECONNECT_DELAY_MS = 1_500;
export const CANVAS_CONNECTION_STABILITY_RESET_MS = 10_000;

export type CanvasConnectionIdentity = {
  ownerId: string;
  generation: number;
};

export const createCanvasConnectionIdentityFactory = (ownerId: string) => {
  const generations = new Map<string, number>();
  return (canvasId: string): CanvasConnectionIdentity => {
    const generation = (generations.get(canvasId) || 0) + 1;
    generations.set(canvasId, generation);
    return { ownerId, generation };
  };
};

export const createCanvasPageOwnerId = (
  randomUuid: () => string | undefined = () =>
    globalThis.crypto?.randomUUID?.(),
  now: () => number = Date.now,
  random: () => number = Math.random,
) =>
  randomUuid() ||
  `page-${now().toString(36)}-${random().toString(36).slice(2)}`;

export const nextCanvasConnectionIdentity =
  createCanvasConnectionIdentityFactory(createCanvasPageOwnerId());

export const shouldReconnectCanvasSocket = (
  closeCode: number,
  disposed: boolean,
  sameOwnerRetryCount: number,
): boolean => {
  if (disposed) return false;
  if (
    closeCode === CANVAS_REPLACED_CLOSE_CODE ||
    closeCode === CANVAS_INVALID_CONNECTION_CLOSE_CODE ||
    closeCode === CANVAS_AUTH_REQUIRED_CLOSE_CODE ||
    closeCode === CANVAS_NOT_FOUND_CLOSE_CODE
  ) {
    return false;
  }
  return (
    closeCode !== CANVAS_SAME_OWNER_REPLACED_CLOSE_CODE ||
    sameOwnerRetryCount < CANVAS_SAME_OWNER_MAX_RETRIES
  );
};
