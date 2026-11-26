const { app, BrowserWindow, ipcMain, shell, nativeImage } = require('electron');
const path = require('path');

let win = null;

function appUrl() {
  const args = process.argv.slice(app.isPackaged ? 1 : 2);
  const url = args.find((a) => /^https?:\/\//.test(a)) || process.env.VDL_URL || 'http://127.0.0.1:8765/';
  return url + (url.includes('?') ? '&' : '?') + 'shell=electron';
}

function createWindow() {
  win = new BrowserWindow({
    width: 1220,
    height: 880,
    minWidth: 860,
    minHeight: 600,
    frame: false,               // 没有系统标题栏，也没有系统边框
    // 和页面壁纸同一个米黄，加载完成前不会闪一下别的颜色
    backgroundColor: '#f4ecdf',
    show: false,
    icon: nativeImage.createFromPath(path.join(__dirname, '..', 'web', 'img', 'favicon-64.png')),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
    },
  });

  win.once('ready-to-show', () => win.show());
  win.loadURL(appUrl());

  // 站外链接交给系统浏览器；登录本来就在 Chrome 里做，扩展照常同步 cookie
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

ipcMain.on('win:min', (e) => e.sender.getOwnerBrowserWindow()?.minimize());
ipcMain.on('win:max', (e) => {
  const w = e.sender.getOwnerBrowserWindow();
  if (!w) return;
  if (w.isMaximized()) w.unmaximize();
  else w.maximize();
  e.reply('win:max-state', w.isMaximized() ? '❐' : '□');
});
ipcMain.on('win:close', (e) => e.sender.getOwnerBrowserWindow()?.close());
ipcMain.on('win:max-state', (e) => e.sender.getOwnerBrowserWindow()?.isMaximized());

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());
