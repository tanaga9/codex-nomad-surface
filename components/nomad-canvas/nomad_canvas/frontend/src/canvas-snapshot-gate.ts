const WEBSOCKET_OPEN = 1;

export const canBroadcastCanvasSnapshot = (
  requested: boolean,
  applyingRemotePatch: boolean,
  websocketReadyState: number | undefined,
) =>
  requested && !applyingRemotePatch && websocketReadyState === WEBSOCKET_OPEN;

export const isRedundantCanvasCheckpoint = (
  includePreview: boolean,
  documentFingerprint: string,
  lastPersistedDocumentFingerprint: string | null,
) =>
  !includePreview &&
  lastPersistedDocumentFingerprint !== null &&
  documentFingerprint === lastPersistedDocumentFingerprint;

export const canProcessCanvasRequest = (applyingRemotePatch: boolean) =>
  !applyingRemotePatch;

export const canCompleteCanvasRequest = (
  startedAtCommitEpoch: number,
  currentCommitEpoch: number,
  applyingRemotePatch: boolean,
) => !applyingRemotePatch && startedAtCommitEpoch === currentCommitEpoch;
