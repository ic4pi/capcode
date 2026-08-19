import React from "react";
import { ArrowRight } from "lucide-react";
import { AmbientBackground } from "@/components/AmbientBackground";

export const LandingPage = ({ onStart, onPricing }) => {
  return (
    <div className="min-h-screen flex flex-col relative overflow-hidden">
      <AmbientBackground />

      <nav className="relative z-10 flex justify-end px-6 pt-6">
        <button
          data-testid="landing-pricing-link"
          onClick={onPricing}
          className="text-text2 hover:text-text text-xs font-mono transition-colors"
        >
          pricing
        </button>
      </nav>

      <main className="flex-1 flex items-center relative z-10">
        <div className="max-w-3xl mx-auto px-6 py-24 w-full">
          <div className="label-xs text-canary mb-6">capcode</div>

          <h1 className="font-display font-extrabold text-4xl sm:text-5xl lg:text-6xl leading-[1.05] text-text">
            Describe the app.<br />
            Get the code.
          </h1>

          <p className="mt-6 text-text2 text-base sm:text-lg leading-relaxed max-w-xl">
            One model writes the spec, another builds it, and you get a working
            app back — ready to run or push to a repo.
          </p>

          <button
            data-testid="landing-start"
            onClick={onStart}
            className="mt-10 inline-flex items-center gap-2 bg-pink text-white px-6 py-3.5 rounded-md font-display font-semibold text-sm hover:bg-pink/90 transition-colors"
          >
            Start building
            <ArrowRight size={16} />
          </button>

          <div className="mt-16 flex flex-wrap gap-x-10 gap-y-3 text-sm text-text2 font-mono">
            <div><span className="text-canary">1.</span> Describe it</div>
            <div><span className="text-canary">2.</span> It gets built</div>
            <div><span className="text-canary">3.</span> Download or push</div>
          </div>

          <div className="mt-10 pt-6 border-t border-line text-xs text-text3 max-w-lg leading-relaxed">
            Free runs a two-model chain — Teacher writes the brief, Artist builds it.
            Paid adds an Architect to plan the structure first and a Reviewer to catch
            issues before you see the result.{" "}
            <button
              data-testid="landing-pricing-inline-link"
              onClick={onPricing}
              className="text-canary hover:underline"
            >
              See plans →
            </button>
          </div>
        </div>
      </main>

      <footer className="text-text3 font-mono text-xs px-6 py-6 border-t border-line relative z-10">
        capcode
      </footer>
    </div>
  );
};
