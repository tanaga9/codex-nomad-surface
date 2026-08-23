export const shouldReconnectTextSocket = (
  closeCode: number,
  destroyed: boolean,
): boolean => !destroyed && closeCode !== 4001;
