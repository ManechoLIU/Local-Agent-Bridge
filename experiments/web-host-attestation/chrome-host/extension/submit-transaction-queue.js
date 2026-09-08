export function createSubmitTransactionQueue() {
  let tail = Promise.resolve();

  return Object.freeze({
    run(task) {
      if (typeof task !== "function") {
        return Promise.reject(new Error("submit transaction task is required"));
      }
      const current = tail.then(task, task);
      tail = current.then(
        function () { return undefined; },
        function () { return undefined; }
      );
      return current;
    }
  });
}
