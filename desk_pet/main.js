const { app, BrowserWindow, screen, ipcMain, Menu, Tray, nativeImage, shell, dialog, clipboard } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const https = require('https');
const { spawn, exec } = require('child_process');

// 单实例锁
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
  process.exit(0);
}

let mainWindow = null;
let tray = null;

const WIN_WIDTH = 320;
const WIN_HEIGHT = 270;

const userDataDir = app.getPath('userData');
const posFile = path.join(userDataDir, 'pet-position.json');
const configFile = path.join(userDataDir, 'pet-config.json');

function loadPos() {
  try {
    return JSON.parse(fs.readFileSync(posFile, 'utf8'));
  } catch {
    return null;
  }
}

function savePos(pos) {
  try {
    fs.writeFileSync(posFile, JSON.stringify(pos));
  } catch {}
}

function loadConfig() {
  const defaults = {
    autoLaunch: true,
    alwaysOnTop: true,
    customAppPath: '',
    customAppName: '',
    campusToken: '',
    campusApiUrl: 'http://localhost:8001',
  };
  try {
    if (fs.existsSync(configFile)) {
      return { ...defaults, ...JSON.parse(fs.readFileSync(configFile, 'utf8')) };
    }
  } catch {}
  return defaults;
}

function saveConfig(cfg) {
  try {
    fs.writeFileSync(configFile, JSON.stringify(cfg, null, 2));
  } catch {}
}

let appConfig = loadConfig();
let lastCampusData = null;

// 开机自启动
function applyAutoLaunch(enable) {
  appConfig.autoLaunch = enable;
  saveConfig(appConfig);

  const regKey = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run';
  const valName = 'CampusDeskPet';

  if (enable) {
    const electronExe = path.join(__dirname, 'node_modules', 'electron', 'dist', 'electron.exe');
    const exeTarget = fs.existsSync(electronExe) ? electronExe : process.execPath;
    const appTarget = path.resolve(__dirname);
    const cmdVal = `\\"${exeTarget}\\" \\"${appTarget}\\"`;

    exec(`reg add "${regKey}" /v "${valName}" /t REG_SZ /d "${cmdVal}" /f`, (err) => {
      if (err) console.error('Failed to set registry autorun:', err);
    });
  } else {
    exec(`reg delete "${regKey}" /v "${valName}" /f`, (err) => {
      if (err) console.error('Failed to delete registry autorun:', err);
    });
  }
}

function findCursorPath() {
  const candidates = [
    'D:\\cursor\\Cursor.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'cursor', 'Cursor.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'cursor', 'Cursor.exe'),
    'C:\\Program Files\\Cursor\\Cursor.exe',
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function findDockerPath() {
  const candidates = [
    'C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe',
    path.join(process.env.PROGRAMFILES || '', 'Docker', 'Docker', 'Docker Desktop.exe'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

async function launchApp(type) {
  let targetPath = null;
  let friendlyName = '';

  if (type === 'cursor') {
    targetPath = findCursorPath();
    friendlyName = 'Cursor';
    if (!targetPath) {
      try {
        spawn('cmd.exe', ['/c', 'start', 'cursor'], { detached: true, stdio: 'ignore' }).unref();
        return { success: true, name: 'Cursor' };
      } catch (err) {
        return { success: false, error: '未找到 Cursor，请确认安装路径' };
      }
    }
  } else if (type === 'docker') {
    targetPath = findDockerPath();
    friendlyName = 'Docker Desktop';
    if (!targetPath) {
      try {
        spawn('cmd.exe', ['/c', 'start', '""', 'Docker Desktop'], { detached: true, stdio: 'ignore' }).unref();
        return { success: true, name: 'Docker' };
      } catch (err) {
        return { success: false, error: '未找到 Docker Desktop，请确认安装路径' };
      }
    }
  } else if (type === 'custom') {
    if (!appConfig.customAppPath) {
      return { success: false, error: '尚未设置自定义软件，请先选择' };
    }
    targetPath = appConfig.customAppPath;
    friendlyName = appConfig.customAppName || '自定义软件';
  }

  if (targetPath) {
    try {
      const ext = path.extname(targetPath).toLowerCase();
      if (ext === '.exe') {
        spawn(targetPath, [], { detached: true, stdio: 'ignore' }).unref();
      } else {
        await shell.openPath(targetPath);
      }
      return { success: true, name: friendlyName };
    } catch (err) {
      return { success: false, error: err.message };
    }
  }

  return { success: false, error: '未知软件类型' };
}

async function chooseCustomApp() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择常用摸鱼/开发软件 (.exe 或快捷方式)',
    filters: [
      { name: '应用程序与快捷方式', extensions: ['exe', 'lnk', 'bat', 'cmd'] },
      { name: '所有文件', extensions: ['*'] }
    ],
    properties: ['openFile']
  });

  if (!result.canceled && result.filePaths.length > 0) {
    const selectedPath = result.filePaths[0];
    const baseName = path.basename(selectedPath, path.extname(selectedPath));
    appConfig.customAppPath = selectedPath;
    appConfig.customAppName = baseName;
    saveConfig(appConfig);
    updateTrayMenu();
    mainWindow?.webContents.send('pet:configChanged', appConfig);
    return { success: true, name: baseName, path: selectedPath };
  }
  return { success: false, canceled: true };
}

// 绑定 / 解绑校园助手同步令牌
async function bindCampusToken() {
  const clip = clipboard.readText().trim();
  const defaultVal = clip && clip.length >= 20 && !clip.includes(' ') ? clip : (appConfig.campusToken || '');

  const choice = await dialog.showMessageBox(mainWindow, {
    type: 'question',
    buttons: ['从剪贴板绑定', '解除绑定', '取消'],
    defaultId: 0,
    cancelId: 2,
    title: '绑定智慧校园助手',
    message: '绑定校园桌宠同步令牌',
    detail: appConfig.campusToken
      ? `当前已绑定令牌：${appConfig.campusToken.slice(0, 8)}... (可去网页端重新生成)\n\n剪贴板当前内容：${clip.slice(0, 30) || '空'}`
      : `请先在网页端「日历/桌宠」点击「生成桌宠令牌」并复制，然后点击下方「从剪贴板绑定」。\n\n剪贴板当前内容：${clip.slice(0, 30) || '空'}`,
  });

  if (choice.response === 0) {
    if (!clip || clip.length < 20) {
      await dialog.showMessageBox(mainWindow, {
        type: 'warning',
        title: '令牌格式有误',
        message: '剪贴板中未检测到有效的桌宠令牌，请先在网页端复制后再试！',
      });
      return;
    }
    appConfig.campusToken = clip;
    saveConfig(appConfig);
    updateTrayMenu();
    mainWindow?.webContents.send('pet:configChanged', appConfig);
    fetchCampusFeed(); // 立即同步一次
    await dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: '绑定成功',
      message: '🎉 校园助手绑定成功！桌宠已开始自动同步你的课程和考试。',
    });
  } else if (choice.response === 1) {
    appConfig.campusToken = '';
    saveConfig(appConfig);
    lastCampusData = null;
    updateTrayMenu();
    mainWindow?.webContents.send('pet:configChanged', appConfig);
    mainWindow?.webContents.send('pet:campusDataUpdated', null);
  }
}

// 获取校园数据
function fetchCampusFeed() {
  if (!appConfig.campusToken) return;
  const baseUrl = (appConfig.campusApiUrl || 'http://localhost:8001').replace(/\/+$/, '');
  const urlStr = `${baseUrl}/api/pet/feed/${appConfig.campusToken}`;

  try {
    const parsed = new URL(urlStr);
    const client = parsed.protocol === 'https:' ? https : http;
    client.get(urlStr, (res) => {
      if (res.statusCode !== 200) {
        return;
      }
      let raw = '';
      res.setEncoding('utf8');
      res.on('data', chunk => { raw += chunk; });
      res.on('end', () => {
        try {
          const data = JSON.parse(raw);
          lastCampusData = data;
          mainWindow?.webContents.send('pet:campusDataUpdated', data);
        } catch (e) {}
      });
    }).on('error', () => {});
  } catch (e) {}
}

function getVirtualBounds() {
  const displays = screen.getAllDisplays();
  let minX = Infinity, minY = Infinity;
  let maxX = -Infinity, maxY = -Infinity;

  for (const d of displays) {
    const { x, y, width, height } = d.bounds;
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + width);
    maxY = Math.max(maxY, y + height);
  }
  return { minX, minY, maxX, maxY };
}

function isPosVisible(pos) {
  if (!pos || typeof pos.x !== 'number' || typeof pos.y !== 'number') return false;
  const displays = screen.getAllDisplays();
  return displays.some(d => {
    const { x, y, width, height } = d.bounds;
    return pos.x >= x - WIN_WIDTH + 60 &&
           pos.x <= x + width - 60 &&
           pos.y >= y - 30 &&
           pos.y <= y + height - 30;
  });
}

function clampPosition(x, y) {
  const { minX, minY, maxX, maxY } = getVirtualBounds();
  const nx = Math.round(Math.max(minX - 60, Math.min(x, maxX - WIN_WIDTH + 60)));
  const ny = Math.round(Math.max(minY, Math.min(y, maxY - WIN_HEIGHT + 30)));
  return { x: nx, y: ny };
}

function createWindow() {
  const savedPos = loadPos();
  let initialX, initialY;

  if (savedPos && isPosVisible(savedPos)) {
    initialX = savedPos.x;
    initialY = savedPos.y;
  } else {
    const primary = screen.getPrimaryDisplay().workArea;
    initialX = primary.x + primary.width - WIN_WIDTH - 20;
    initialY = primary.y + primary.height - WIN_HEIGHT - 20;
  }

  mainWindow = new BrowserWindow({
    width: WIN_WIDTH,
    height: WIN_HEIGHT,
    x: initialX,
    y: initialY,
    transparent: true,
    frame: false,
    resizable: false,
    alwaysOnTop: appConfig.alwaysOnTop,
    skipTaskbar: true,
    type: 'toolbar',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    hasShadow: false,
    backgroundColor: '#00000000',
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  if (process.argv.includes('--dev')) {
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  }

  mainWindow.setIgnoreMouseEvents(true, { forward: true });

  mainWindow.on('moved', () => {
    if (mainWindow) {
      const [x, y] = mainWindow.getPosition();
      savePos({ x, y });
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  return mainWindow;
}

function buildMenuTemplate() {
  const customLabel = appConfig.customAppName
    ? `⭐ 打开 ${appConfig.customAppName}`
    : '⭐ 打开 自定义软件 (未设置)';

  const campusStatusLabel = appConfig.campusToken
    ? '🎓 校园助手：已绑定 (点击解绑或换绑)'
    : '🎓 绑定校园助手 (同步今日课表/考试)...';

  return [
    {
      label: campusStatusLabel,
      click: () => bindCampusToken(),
    },
    { type: 'separator' },
    {
      label: '💻 打开 Cursor',
      click: () => launchApp('cursor'),
    },
    {
      label: '🐳 打开 Docker Desktop',
      click: () => launchApp('docker'),
    },
    {
      label: customLabel,
      enabled: !!appConfig.customAppPath,
      click: () => launchApp('custom'),
    },
    {
      label: '⚙️ 选择/更换自定义软件...',
      click: () => chooseCustomApp(),
    },
    { type: 'separator' },
    {
      label: '💬 摸鱼与快捷启动气泡',
      click: () => {
        mainWindow?.webContents.send('pet:showAppBubble');
      },
    },
    {
      label: '📚 查看今日课表与考试',
      click: () => {
        mainWindow?.webContents.send('pet:showScheduleBubble');
      },
    },
    { type: 'separator' },
    {
      label: '🚀 开机自启动',
      type: 'checkbox',
      checked: appConfig.autoLaunch,
      click: (item) => {
        applyAutoLaunch(item.checked);
        updateTrayMenu();
      },
    },
    {
      label: '📌 始终置顶',
      type: 'checkbox',
      checked: appConfig.alwaysOnTop,
      click: (item) => {
        appConfig.alwaysOnTop = item.checked;
        saveConfig(appConfig);
        mainWindow?.setAlwaysOnTop(item.checked);
        updateTrayMenu();
      },
    },
    { type: 'separator' },
    {
      label: '❌ 退出桌宠',
      click: () => {
        app.quit();
      },
    },
  ];
}

function updateTrayMenu() {
  if (tray) {
    tray.setContextMenu(Menu.buildFromTemplate(buildMenuTemplate()));
  }
}

function createTray() {
  const iconPath = path.join(__dirname, 'assets', 'icon.png');
  let icon;
  if (fs.existsSync(iconPath)) {
    icon = nativeImage.createFromPath(iconPath);
    if (!icon.isEmpty()) {
      icon = icon.resize({ width: 24, height: 24 });
    }
  } else {
    icon = nativeImage.createEmpty();
  }

  tray = new Tray(icon);
  tray.setToolTip('校园桌宠 🐾 (双击显示/右键菜单)');
  updateTrayMenu();

  tray.on('double-click', () => {
    if (mainWindow) {
      mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show();
    }
  });
}

// IPC 交互
ipcMain.on('pet:move', (_event, { x, y }) => {
  if (mainWindow) {
    const clamped = clampPosition(x, y);
    mainWindow.setPosition(clamped.x, clamped.y, false);
    savePos(clamped);
  }
});

ipcMain.handle('pet:getPos', () => {
  if (mainWindow) {
    const [x, y] = mainWindow.getPosition();
    return { x, y };
  }
  return { x: 0, y: 0 };
});

ipcMain.on('pet:setIgnoreMouseEvents', (event, ignore, options) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  win?.setIgnoreMouseEvents(ignore, options);
});

ipcMain.on('pet:setAlwaysOnTop', (_event, flag) => {
  appConfig.alwaysOnTop = flag;
  saveConfig(appConfig);
  mainWindow?.setAlwaysOnTop(flag);
});

ipcMain.on('pet:showContextMenu', () => {
  if (mainWindow) {
    const menu = Menu.buildFromTemplate(buildMenuTemplate());
    menu.popup({ window: mainWindow });
  }
});

ipcMain.handle('pet:openApp', async (_event, appType) => {
  return await launchApp(appType);
});

ipcMain.handle('pet:chooseCustomApp', async () => {
  return await chooseCustomApp();
});

ipcMain.handle('pet:bindCampus', async () => {
  await bindCampusToken();
  return appConfig;
});

ipcMain.handle('pet:getConfig', () => {
  return appConfig;
});

ipcMain.handle('pet:getCampusData', () => {
  return lastCampusData;
});

ipcMain.handle('pet:refreshCampus', () => {
  fetchCampusFeed();
  return true;
});

ipcMain.handle('pet:toggleAutoLaunch', () => {
  applyAutoLaunch(!appConfig.autoLaunch);
  updateTrayMenu();
  return appConfig.autoLaunch;
});

app.whenReady().then(() => {
  applyAutoLaunch(appConfig.autoLaunch);

  createWindow();
  createTray();

  // 定时每 15 分钟拉取一次校园课表与考试数据
  fetchCampusFeed();
  setInterval(() => {
    fetchCampusFeed();
  }, 15 * 60 * 1000);

  const ensureVisible = () => {
    if (!mainWindow) return;
    const [cx, cy] = mainWindow.getPosition();
    if (!isPosVisible({ x: cx, y: cy })) {
      const primary = screen.getPrimaryDisplay().workArea;
      const nx = primary.x + primary.width - WIN_WIDTH - 20;
      const ny = primary.y + primary.height - WIN_HEIGHT - 20;
      mainWindow.setPosition(nx, ny);
      savePos({ x: nx, y: ny });
    }
  };

  screen.on('display-metrics-changed', ensureVisible);
  screen.on('display-removed', ensureVisible);
  screen.on('display-added', ensureVisible);

  app.on('activate', () => {
    if (!mainWindow) createWindow();
  });
});

app.on('window-all-closed', (e) => {
  e.preventDefault();
});

app.on('second-instance', () => {
  if (mainWindow) {
    mainWindow.show();
    mainWindow.focus();
  }
});
