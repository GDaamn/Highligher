// Persist originals separately so listing books never loads the PDF bytes.
let connection;
function database() {
  connection ??= new Promise((resolve, reject) => {
    const request = indexedDB.open("highlighter-device-library", 1);
    request.onupgradeneeded = () => {
      for (const name of ["books", "files", "pages", "preferences"]) request.result.createObjectStore(name);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => { connection = null; reject(request.error); };
  });
  return connection;
}

async function transact(stores, mode, operation) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(stores, mode);
    const request = operation(tx);
    tx.oncomplete = () => resolve(request?.result);
    tx.onabort = () => reject(tx.error || new Error("Browser storage is unavailable."));
  });
}

export function readDevice(store, key) {
  return transact([store], "readonly", tx => tx.objectStore(store).get(key));
}

export function listDeviceBooks() {
  return transact(["books"], "readonly", tx => tx.objectStore("books").getAll());
}

export function saveDeviceBook(record, file) {
  return transact(["books", "files"], "readwrite", tx => {
    tx.objectStore("books").put(record, record.id);
    tx.objectStore("files").put(file, record.id);
  });
}

export function cacheDevicePage(id, page) {
  return transact(["books", "pages"], "readwrite", tx => {
    const books = tx.objectStore("books");
    const exists = books.get(id);
    exists.onsuccess = () => {
      if (!exists.result) return;
      tx.objectStore("pages").put(page, [id, page.number]);
      if (page.thumbnail && !exists.result.thumbnail) books.put({...exists.result, thumbnail:page.thumbnail}, id);
    };
  });
}

export function removeDeviceBook(id) {
  return transact(["books", "files", "preferences", "pages"], "readwrite", tx => {
    for (const name of ["books", "files", "preferences"]) tx.objectStore(name).delete(id);
    tx.objectStore("pages").delete(IDBKeyRange.bound([id, 0], [id, Infinity]));
  });
}

export function changePreferences(id, update) {
  return transact(["preferences", "books"], "readwrite", tx => {
    const exists = tx.objectStore("books").getKey(id);
    exists.onsuccess = () => {
      if (!exists.result) return;
      const store = tx.objectStore("preferences");
      const previous = store.get(id);
      previous.onsuccess = () => {
        const prefs = previous.result || {};
        update(prefs);
        store.put(prefs, id);
      };
    };
  });
}
