"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useServerConfig } from "@/app/hooks/useServerConfig";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const pretty = (name: string) => name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export function SessionDialog({ open, onOpenChange }: Props) {
  const { load, save } = useServerConfig();
  const [available, setAvailable] = useState<string[] | null>(null);
  const [active, setActive] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    void load().then((res) => {
      if (res.sessions) {
        setAvailable(res.sessions.available);
        setActive(res.sessions.active);
      }
    });
  }, [open, load]);

  const toggle = (name: string) => setActive((a) => (a.includes(name) ? a.filter((n) => n !== name) : [...a, name]));

  const handleSave = async () => {
    if (!active.length) {
      toast.error("Select at least one session");
      return;
    }
    setSaving(true);
    try {
      const res = await save({ sessions: { active } });
      if (res.ok) {
        toast.success("Sessions saved to config/sessions.yaml");
        onOpenChange(false);
      } else {
        toast.error((res.errors ?? ["Save failed"]).join("; "));
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>Trading sessions</DialogTitle>
          <DialogDescription>
            Trade only when the market is in an enabled session (a bar trades if it falls in ANY of them). Saved to config/sessions.yaml; used by the next run and the CLI. Edit each session&apos;s hours in that file.
          </DialogDescription>
        </DialogHeader>
        {available ? (
          <div className="flex flex-wrap gap-2 py-4">
            {available.map((name) => {
              const on = active.includes(name);
              return (
                <Button key={name} type="button" variant={on ? "default" : "outline"} size="sm" aria-pressed={on} onClick={() => toggle(name)}>
                  {pretty(name)}
                </Button>
              );
            })}
          </div>
        ) : (
          <div className="py-8 text-center text-sm text-muted-foreground">Loading sessions…</div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!available || !active.length || saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
