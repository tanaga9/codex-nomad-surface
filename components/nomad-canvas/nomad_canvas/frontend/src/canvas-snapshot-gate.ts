const WEBSOCKET_OPEN = 1;

export const canBroadcastCanvasSnapshot = (
  requested: boolean,
  applyingRemotePatch: boolean,
  websocketReadyState: number | undefined,
) =>
  requested && !applyingRemotePatch && websocketReadyState === WEBSOCKET_OPEN;

export const canProcessCanvasRequest = (applyingRemotePatch: boolean) =>
  !applyingRemotePatch;

export const canCompleteCanvasRequest = (
  startedAtCommitEpoch: number,
  currentCommitEpoch: number,
  applyingRemotePatch: boolean,
) => !applyingRemotePatch && startedAtCommitEpoch === currentCommitEpoch;
