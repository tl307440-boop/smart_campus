const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('petAPI', {
  // 窗口移动与位置
  move: (x, y) => ipcRenderer.send('pet:move', { x, y }),
  getPos: () => ipcRenderer.invoke('pet:getPos'),
  
  // 鼠标穿透
  setIgnoreMouseEvents: (ignore, options) => {
    ipcRenderer.send('pet:setIgnoreMouseEvents', ignore, options);
  },

  // 始终置顶
  setAlwaysOnTop: (flag) => ipcRenderer.send('pet:setAlwaysOnTop', flag),

  // 右键菜单
  showContextMenu: () => ipcRenderer.send('pet:showContextMenu'),

  // 打开软件
  openApp: (appType) => ipcRenderer.invoke('pet:openApp', appType),

  // 选择自定义软件
  chooseCustomApp: () => ipcRenderer.invoke('pet:chooseCustomApp'),

  // 绑定校园助手
  bindCampus: () => ipcRenderer.invoke('pet:bindCampus'),

  // 获取配置
  getConfig: () => ipcRenderer.invoke('pet:getConfig'),

  // 获取校园课表/考试数据
  getCampusData: () => ipcRenderer.invoke('pet:getCampusData'),
  refreshCampus: () => ipcRenderer.invoke('pet:refreshCampus'),

  // 切换开机自启动
  toggleAutoLaunch: () => ipcRenderer.invoke('pet:toggleAutoLaunch'),

  // 事件监听
  onShowAppBubble: (callback) => {
    ipcRenderer.on('pet:showAppBubble', () => callback());
  },
  onShowScheduleBubble: (callback) => {
    ipcRenderer.on('pet:showScheduleBubble', () => callback());
  },
  onConfigChanged: (callback) => {
    ipcRenderer.on('pet:configChanged', (_event, cfg) => callback(cfg));
  },
  onCampusDataUpdated: (callback) => {
    ipcRenderer.on('pet:campusDataUpdated', (_event, data) => callback(data));
  },
});
