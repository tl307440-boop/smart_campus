# 校园桌宠 🐾

从 smart_campus 项目抽离的独立桌面桌宠，基于 **Electron** 构建。

## 特性

- 🪟 透明无边框窗口，始终置顶
- 👀 眼睛实时跟随鼠标
- 😴 闲置 6 秒后睡觉，动一下就醒来
- 👆 单击戳一戳 → 跳跳乐 + 说话泡
- ✋ 在头顶来回滑动 → 摸头 + 撒心心
- 🖱️ 拖动随意放置，下次启动记忆位置
- 🌀 连戳 4 下 → 晕圈圈
- 双击 → 随机说一句鼓励的话
- 系统托盘图标，右键可显示/隐藏/退出

## 快速启动

```bash
cd desk_pet
npm install
npm start
```

## 打包为单文件 exe（Windows）

```bash
npm run build
# 输出在 dist/ 目录
```

## 文件结构

```
desk_pet/
├── main.js          # Electron 主进程（窗口/托盘/IPC）
├── preload.js       # 安全桥接（contextBridge）
├── package.json
├── assets/
│   └── icon.png     # 托盘图标（可替换）
└── renderer/
    ├── index.html   # 桌宠 HTML（透明 SVG）
    ├── pet.js       # 桌宠逻辑（从 useDeskPet.ts 移植）
    └── girl.webp    # 桌宠图片（拷贝自原项目）
```

## 注意

原项目（`smart_campus`）代码**完全未改动**，本目录完全独立。
