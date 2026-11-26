const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('appControls', {
  minimize: () => ipcRenderer.send('win:min'),
  toggleMax: () => new Promise((resolve) => {
    ipcRenderer.once('win:max-state', (_, mark) => resolve(mark));
    ipcRenderer.send('win:max');
  }),
  close: () => ipcRenderer.send('win:close'),
  onMaxState: (cb) => ipcRenderer.on('win:max-state', (_, mark) => cb(mark)),
});
