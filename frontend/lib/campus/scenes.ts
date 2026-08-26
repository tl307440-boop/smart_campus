/**
 * 武汉文理学院官网「校园风光」本地副本。
 * 图册：https://www.whwl.edu.cn/xxgk/xyfg.htm
 * 本校校园助手用，不热链官网 CDN。
 */
export const CAMPUS_SCENES = {
  herun: {
    id: "herun",
    src: "/campus/herun.jpg",
    title: "和润楼",
    caption: "主楼远眺 · 蓝天与林荫道",
    width: 1024,
    height: 682,
    sourceUrl:
      "https://www.whwl.edu.cn/__local/3/B7/82/5D261F4FB4A935A23B9E7F99560_90BAA06F_21F10.jpg",
    pageUrl: "https://www.whwl.edu.cn/info/1007/3333.htm",
  },
  heqin: {
    id: "heqin",
    src: "/campus/heqin.jpg",
    title: "和沁楼",
    caption: "教学楼群 · 庭院与春树",
    width: 1024,
    height: 767,
    sourceUrl:
      "https://www.whwl.edu.cn/__local/F/0F/99/A9CBC266BB32B3336BC2D6583B7_C2AB7CE0_29235.jpg",
    pageUrl: "https://www.whwl.edu.cn/info/1007/3335.htm",
  },
  aerial: {
    id: "aerial",
    src: "/campus/aerial.jpg",
    title: "校园全景",
    caption: "跑道、湖面与楼群",
    width: 1920,
    height: 420,
    sourceUrl: "https://www.whwl.edu.cn/images/111_fuben.jpg",
    pageUrl: "https://www.whwl.edu.cn/xxgk/xyfg.htm",
  },
  library: {
    id: "library",
    src: "/campus/library.jpg",
    title: "图书馆",
    caption: "阅览长廊",
    width: 1024,
    height: 682,
    sourceUrl:
      "https://www.whwl.edu.cn/__local/A/CD/3E/6137797A71F460665BAEA7DE533_7BF4CF52_1C8DA.jpg",
    pageUrl: "https://www.whwl.edu.cn/info/1007/3353.htm",
  },
  hepai: {
    id: "hepai",
    src: "/campus/hepai.jpg",
    title: "和湃苑",
    caption: "生活区林荫道",
    width: 1024,
    height: 682,
    sourceUrl:
      "https://www.whwl.edu.cn/__local/1/2A/99/2F8BEDAC5950AEF959DF22D7CED_0FA504C0_1C6DF.jpg",
    pageUrl: "https://www.whwl.edu.cn/info/1007/3368.htm",
  },
  hesu: {
    id: "hesu",
    src: "/campus/hesu.jpg",
    title: "和溯楼",
    caption: "蓝窗格教学楼",
    width: 1024,
    height: 682,
    sourceUrl:
      "https://www.whwl.edu.cn/__local/0/9E/37/D0D03CC85308E89096690C012FA_A5317D4E_1ECF4.jpg",
    pageUrl: "https://www.whwl.edu.cn/info/1007/3339.htm",
  },
} as const;

export type CampusSceneId = keyof typeof CAMPUS_SCENES;
export type CampusScene = (typeof CAMPUS_SCENES)[CampusSceneId];

export const SCENE_PLACEMENT = {
  authHero: "herun",
  sidebar: "herun",
  chatHeader: "aerial",
} as const satisfies Record<string, CampusSceneId>;

export const AUTH_STRIP_IDS = [
  "herun",
  "heqin",
  "aerial",
  "library",
] as const satisfies ReadonlyArray<CampusSceneId>;

export const SCHOOL_MARK = {
  name: "武汉文理学院",
  motto: "融文汇理 · 立德树人",
  place: "滠水湖畔 · 长江新区",
  galleryUrl: "https://www.whwl.edu.cn/xxgk/xyfg.htm",
} as const;
