"use client";

import { useState } from "react";
import { ServicePane } from "@/components/AgentSettings";
import { CalendarPane } from "@/components/CalendarPane";
import { ImPushPane } from "@/components/ImPushPane";
import { JwAccountPane } from "@/components/JwAccountPane";
import { KnowledgePane } from "@/components/KnowledgePane";
import { WorkspacePane } from "@/components/WorkspaceAdmin";

type ConnectSubTab = "im" | "jw" | "service" | "admin";

interface Props {
  jwOn: boolean;
  isStaff: boolean;
  isAdmin: boolean;
  onJwUpdated: () => void;
  onWorkspaceSaved: () => void;
}

export function ConnectPane({ jwOn, isStaff, isAdmin, onJwUpdated, onWorkspaceSaved }: Props) {
  const [subTab, setSubTab] = useState<ConnectSubTab>("im");

  const tabs: { id: ConnectSubTab; label: string }[] = [
    { id: "im", label: "企微 / 手机端" },
    { id: "jw", label: "教务与日历" },
    { id: "service", label: "AI 服务" },
    ...((isStaff || isAdmin) ? [{ id: "admin" as const, label: "校方后台" }] : []),
  ];

  return (
    <div className="space-y-5 pb-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-2xl leading-tight">连接设置</h1>
          <p className="text-sm text-muted">绑定企微实现手机免登录直聊、接入教务知情上下文与模型配置</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                setSubTab(t.id);
                if (subTab === "jw" && t.id !== "jw") onJwUpdated();
              }}
              className={`btn btn-sm ${subTab === t.id ? "btn-primary" : ""}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {subTab === "im" && <ImPushPane />}

      {subTab === "jw" && (
        <div className="space-y-6">
          <JwAccountPane />
          {jwOn && (
            <div className="border-t-2 border-dashed border-rule pt-6">
              <CalendarPane />
            </div>
          )}
        </div>
      )}

      {subTab === "service" && <ServicePane isAdmin={isAdmin} />}

      {subTab === "admin" && (isStaff || isAdmin) && (
        <div className="space-y-8">
          {isStaff && <KnowledgePane />}
          {isAdmin && (
            <div className={isStaff ? "border-t-2 border-dashed border-rule pt-6" : ""}>
              <WorkspacePane onSaved={onWorkspaceSaved} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
