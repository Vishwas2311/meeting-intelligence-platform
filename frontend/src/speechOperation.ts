/** SDK callbacks can fail to arrive when the connection or device disappears. */
export function speechOperation(
  action: (success: () => void, failure: (error: string) => void) => void,
  timeoutMs: number,
): Promise<void> {
  let timer: ReturnType<typeof setTimeout>;
  return new Promise<void>((resolve, reject) => {
    timer = setTimeout(
      () => reject(new Error('Speech operation timed out. Check the connection and retry.')),
      timeoutMs,
    );
    action(resolve, (error) => reject(new Error(String(error))));
  }).finally(() => clearTimeout(timer));
}
