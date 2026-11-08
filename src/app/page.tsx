"use client";

import React, { useState, useEffect, useCallback, Suspense } from "react";
import { useQueryState } from "nuqs";
import {
  Loader2,
  Settings,
  CandlestickChart,
  FlaskConical,
  SlidersHorizontal,
} from "lucide-react";
import { Assistant } from "@langchain/langgraph-sdk";
import {
  DEFAULT_CONFIG,
  getConfig,
  saveConfig,
  testConnection,
  StandaloneConfig,
} from "@/lib/config";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ClientProvider, useClient } from "@/providers/ClientProvider";
import { ChatProvider } from "@/providers/ChatProvider";
import { AccountProvider } from "@/providers/AccountProvider";
import { ConnectionSettings } from "@/app/components/ConnectionSettings";
import { StatusBar } from "@/app/components/StatusBar";
import { RunLauncher } from "@/app/components/RunLauncher";
import { TradingDashboard } from "@/app/components/TradingDashboard";
import { ChartTab } from "@/app/components/ChartTab";
import { AccountTab } from "@/app/components/AccountTab";
import { BacktestLoopTab } from "@/app/components/BacktestLoopTab";
import { SettingsTab } from "@/app/components/SettingsTab";

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

function HomePageInner({
  config,
  onSaveConfig,
}: {
  config: StandaloneConfig;
  onSaveConfig: (c: StandaloneConfig) => void;
}) {
  const client = useClient();
  const [tab, setTab] = useQueryState("tab", { defaultValue: "trade" });
  const [, setSection] = useQueryState("section");
  const [assistant, setAssistant] = useState<Assistant | null>(null);

  // Resolve the graph's default (system-created) assistant; fall back to using the graph id directly.
  useEffect(() => {
    const fallback: Assistant = {
      assistant_id: config.assistantId,
      graph_id: config.assistantId,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      config: {},
      metadata: {},
      version: 1,
      name: config.assistantId,
      context: {},
    };
    client.assistants
      .search({ graphId: config.assistantId, limit: 100 })
      .then((list) =>
        setAssistant(
          list.find((a) => a.metadata?.["created_by"] === "system") ?? fallback
        )
      )
      .catch(() => setAssistant(fallback)); // the header's connection dot reports the outage
  }, [client, config.assistantId]);

  const openConnection = () => {
    void setTab("settings");
    void setSection("connection");
  };

  return (
    <AccountProvider>
      <ChatProvider activeAssistant={assistant}>
        <div className="flex h-screen flex-col">
          <header className="flex min-h-14 flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-2">
            <h1 className="text-lg font-semibold">Trading Dashboard</h1>
            <StatusBar onOpenConnection={openConnection} />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void setTab("settings")}
              aria-label="Settings"
            >
              <Settings className="h-4 w-4" />
            </Button>
          </header>

          <Tabs
            value={tab}
            onValueChange={(v) => void setTab(v)}
            className="flex min-h-0 flex-1 flex-col gap-0"
          >
            <TabsList className="mx-4 mt-3 self-start">
              <TabsTrigger value="trade">
                <CandlestickChart className="mr-1.5 h-4 w-4" /> Trade
              </TabsTrigger>
              <TabsTrigger value="research">
                <FlaskConical className="mr-1.5 h-4 w-4" /> Research
              </TabsTrigger>
              <TabsTrigger value="settings">
                <SlidersHorizontal className="mr-1.5 h-4 w-4" /> Settings
              </TabsTrigger>
            </TabsList>

            <TabsContent
              value="trade"
              className="mt-0 min-h-0 flex-1 overflow-y-auto"
            >
              <div className="flex flex-col gap-6 p-4">
                <Section title="Start a run">
                  <RunLauncher />
                </Section>
                <div className="h-[60vh] min-h-[420px] overflow-hidden rounded-lg border border-border">
                  <ChartTab />
                </div>
                <Section title="This run">
                  <TradingDashboard />
                </Section>
                <Section title="Account">
                  <AccountTab />
                </Section>
              </div>
            </TabsContent>
            <TabsContent
              value="research"
              className="mt-0 min-h-0 flex-1"
            >
              <BacktestLoopTab />
            </TabsContent>
            <TabsContent
              value="settings"
              className="mt-0 min-h-0 flex-1"
            >
              <SettingsTab
                config={config}
                onSaveConfig={onSaveConfig}
              />
            </TabsContent>
          </Tabs>
        </div>
      </ChatProvider>
    </AccountProvider>
  );
}

function HomePageContent() {
  // undefined = still checking; null = no working config yet (first run and the default server isn't up)
  const [config, setConfig] = useState<StandaloneConfig | null | undefined>(
    undefined
  );
  const [defaultErr, setDefaultErr] = useState<string | null>(null);

  // Saved config wins. Otherwise try the local default silently, so a standard setup needs no dialog.
  useEffect(() => {
    const saved = getConfig();
    if (saved) {
      setConfig(saved);
      return;
    }
    testConnection(DEFAULT_CONFIG).then((err) => {
      if (err) {
        setDefaultErr(err);
        setConfig(null);
      } else {
        saveConfig(DEFAULT_CONFIG);
        setConfig(DEFAULT_CONFIG);
      }
    });
  }, []);

  const handleSaveConfig = useCallback((newConfig: StandaloneConfig) => {
    saveConfig(newConfig);
    setConfig(newConfig);
  }, []);

  if (config === undefined) {
    return (
      <div className="flex h-screen items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Connecting to the trading
        server…
      </div>
    );
  }

  if (config === null) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="w-full max-w-2xl">
          <h1 className="text-2xl font-bold">
            Welcome to the Trading Dashboard
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">{defaultErr}</p>
          <div className="mt-6 rounded-lg border border-border p-4">
            <ConnectionSettings onSave={handleSaveConfig} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <ClientProvider
      deploymentUrl={config.deploymentUrl}
      apiKey={
        config.langsmithApiKey ||
        process.env.NEXT_PUBLIC_LANGSMITH_API_KEY ||
        ""
      }
    >
      <HomePageInner
        config={config}
        onSaveConfig={handleSaveConfig}
      />
    </ClientProvider>
  );
}

export default function HomePage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center">
          <p className="text-muted-foreground">Loading...</p>
        </div>
      }
    >
      <HomePageContent />
    </Suspense>
  );
}
