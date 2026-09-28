import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { RotateCcw, Wand2 } from "lucide-react";
import { useFeedback } from "@/src/feedback/FeedbackProvider";
import UserVaultTab from "./UserVaultTab";
import ModelPreferencesTab from "./ModelPreferencesTab";
import { useTour } from "@/src/lib/tour/TourProvider";

type Tab = "general" | "models" | "vault";

const TAB_LABELS: Record<Tab, string> = {
  general: "General",
  models: "Models",
  vault: "User Vault",
};

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<Tab>("general");
  const navigate = useNavigate();
  const { startTour } = useTour();
  const { isAdmin } = useFeedback();

  const handleReplayTour = () => {
    navigate("/");
    window.setTimeout(() => startTour(true), 150);
  };

  return (
    <div className="flex flex-col flex-1 h-full">
      {/* Tab bar */}
      <div className="flex items-center gap-1 px-6 pt-4 pb-0 border-b border-slate-200 bg-white shrink-0">
        {(["general", "models", "vault"] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-semibold rounded-t-md transition-colors border-b-2 -mb-px ${
              activeTab === tab
                ? "text-slate-900 border-slate-900"
                : "text-slate-500 border-transparent hover:text-slate-700"
            }`}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {activeTab === "general" && (
        <div className="flex-1 overflow-y-auto bg-[#F9FAFB]">
          <div className="max-w-2xl mx-auto px-8 py-12 space-y-6">
            <div>
              <h2 className="text-lg font-bold text-slate-900">Product tour</h2>
              <p className="mt-1 text-sm text-slate-500">
                Walk through sidebar navigation and home shortcuts anytime.
              </p>
            </div>
            <button
              type="button"
              onClick={handleReplayTour}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-white border border-slate-200 text-sm font-semibold text-slate-700 hover:bg-slate-50 transition-colors"
            >
              <RotateCcw className="w-4 h-4 text-[#FF444F]" />
              Replay Product Tour
            </button>

            {isAdmin && (
              <div className="pt-6 border-t border-slate-200">
                <h2 className="text-lg font-bold text-slate-900">Platform setup</h2>
                <p className="mt-1 text-sm text-slate-500">
                  Re-run the setup wizard to change branding, model gateway, identity, or directory settings.
                </p>
                <Link
                  to="/admin/setup?rerun=1"
                  className="mt-3 inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-white border border-slate-200 text-sm font-semibold text-slate-700 hover:bg-slate-50 transition-colors"
                >
                  <Wand2 className="w-4 h-4 text-[#FF444F]" />
                  Open setup wizard
                </Link>
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === "models" && (
        <div className="flex-1 overflow-y-auto bg-[#F9FAFB]">
          <ModelPreferencesTab />
        </div>
      )}

      {activeTab === "vault" && (
        <div className="flex-1 overflow-y-auto bg-[#F9FAFB]">
          <UserVaultTab />
        </div>
      )}
    </div>
  );
}
