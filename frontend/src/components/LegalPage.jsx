import React from "react";
import { ArrowLeft } from "lucide-react";
import { AmbientBackground } from "@/components/AmbientBackground";

const Section = ({ title, children, testid }) => (
  <div className="mt-8" data-testid={testid}>
    <h2 className="font-display font-semibold text-lg text-text mb-2">{title}</h2>
    <div className="text-text2 text-sm leading-relaxed space-y-2">{children}</div>
  </div>
);

const LAST_UPDATED = "August 2026";

const PRIVACY = (
  <>
    <Section title="Overview" testid="privacy-overview">
      <p>
        CapCode ("CapCode", "we", "us") builds working apps from a text prompt
        using a chain of AI models. This policy explains what we collect, why,
        and how it's used.
      </p>
    </Section>
    <Section title="Information We Collect" testid="privacy-collect">
      <p>
        <strong className="text-text">Account info:</strong> if you sign in with
        Google, we receive your name, email, and profile picture from Google via
        our auth provider (Neon Auth).
      </p>
      <p>
        <strong className="text-text">Anonymous session:</strong> if you don't
        sign in, a random session id is stored in your browser to keep your
        settings and builds tied to that browser only.
      </p>
      <p>
        <strong className="text-text">Your prompts & builds:</strong> every
        target prompt you submit and every generation it produces (code, specs,
        scores) is stored so you can view your build history.
      </p>
      <p>
        <strong className="text-text">Optional BYOK keys & GitHub token:</strong>
        {" "}if you paste your own OpenRouter/Venice/NVIDIA API key or a GitHub
        personal access token, it's stored against your session/account to make
        those calls on your behalf. We never display a saved key back to you.
      </p>
      <p>
        <strong className="text-text">Payment info:</strong> handled entirely by
        Stripe — we never see or store your card number. We keep a record of
        your subscription tier, trial status, and credit balance.
      </p>
      <p>
        <strong className="text-text">Usage logs:</strong> which provider/model
        was used and how many tokens, for billing and abuse-prevention purposes.
      </p>
    </Section>
    <Section title="How We Use It" testid="privacy-use">
      <p>
        To run the pipeline, show you your build history, enforce tier/rate
        limits, process payments and credits, and improve the product. Verified
        builds may be used (anonymized) as exemplars to improve future builds
        for all users.
      </p>
    </Section>
    <Section title="Who We Share It With" testid="privacy-thirdparty">
      <p>
        Your prompts are sent to whichever LLM provider is assigned to a role —
        NVIDIA NIM, OpenRouter, Venice, or Groq — solely to generate that build.
        Payments go through Stripe. Sign-in goes through Google via Neon Auth.
        Data is stored on Neon's managed Postgres. We do not sell your data.
      </p>
    </Section>
    <Section title="Uncensored / Venice Mode" testid="privacy-uncensored">
      <p>
        Enabling uncensored mode routes your prompt to Venice's less-filtered
        models and requires a separate liability waiver — see the "uncensored
        mode" consent screen in Settings for that specific disclosure. This
        privacy policy governs data handling only; it does not limit your
        responsibility under that waiver.
      </p>
    </Section>
    <Section title="Data Retention & Deletion" testid="privacy-retention">
      <p>
        We keep your data as long as your account/session is active. You can
        ask us to delete your account data by reaching out through the app —
        we don't yet have a dedicated privacy inbox, so use whatever contact
        channel CapCode currently provides you.
      </p>
    </Section>
    <Section title="Children" testid="privacy-children">
      <p>CapCode is not directed at children and is not intended for users under 16.</p>
    </Section>
    <Section title="Changes" testid="privacy-changes">
      <p>We may update this policy as the product changes. Material changes will be reflected here with a new "last updated" date.</p>
    </Section>
  </>
);

const TERMS = (
  <>
    <Section title="Acceptance of Terms" testid="terms-acceptance">
      <p>
        By using CapCode you agree to these Terms. If you don't agree, don't use
        the service.
      </p>
    </Section>
    <Section title="The Service" testid="terms-service">
      <p>
        CapCode runs a chain of AI models (Teacher, Architect, Artist, Reviewer,
        Rater, Corrector) against a prompt you provide and returns generated
        code. Free/trial/paid tiers run different subsets of that chain with
        different providers and token limits, as described on the Pricing page.
      </p>
    </Section>
    <Section title="Accounts" testid="terms-accounts">
      <p>
        You can use CapCode anonymously (browser-scoped) or sign in with Google
        for a persistent account, billing, and trial tracking. You're
        responsible for anything done through your account.
      </p>
    </Section>
    <Section title="Payments, Trials & Credits" testid="terms-payments">
      <p>
        The paid plan is a $16/mo (or discounted annual) subscription billed via
        Stripe, cancel anytime. The 7-day trial is a $2.99 one-time charge, not
        a subscription. Credit top-ups ($8/500, $16/1000, $32/2000 credits) are
        one-time, non-refundable purchases that add to your credit balance and
        don't expire on your monthly cycle renewal. Subscription credits reset
        each billing cycle and do not roll over. All charges are processed by
        Stripe; we don't store your card details.
      </p>
    </Section>
    <Section title="Acceptable Use" testid="terms-acceptable-use">
      <p>
        Don't use CapCode to generate content that's illegal, infringing, or
        intended to cause harm. We can suspend or terminate access for abuse,
        excessive load, or violation of these terms.
      </p>
    </Section>
    <Section title="Uncensored Mode & Your Responsibility" testid="terms-uncensored">
      <p>
        Uncensored/Venice-routed generation requires a separate waiver
        acceptance at the point you enable it. By accepting that waiver you
        confirm you are solely responsible for anything generated in that mode
        and everything it's subsequently used for — CapCode, its operators, and
        upstream model providers accept no responsibility for uncensored
        outputs or their downstream effects.
      </p>
    </Section>
    <Section title="Your Content" testid="terms-content">
      <p>
        You own the code generated from your own prompts, to the extent the law
        allows. CapCode makes no warranty that generated code is correct,
        secure, or fit for any particular purpose — review it before you rely
        on it.
      </p>
    </Section>
    <Section title="No Warranty / Limitation of Liability" testid="terms-liability">
      <p>
        The service is provided "as is" without warranties of any kind. To the
        maximum extent permitted by law, CapCode's total liability for any
        claim is limited to the amount you paid us in the 12 months before the
        claim arose.
      </p>
    </Section>
    <Section title="Termination" testid="terms-termination">
      <p>
        You can stop using CapCode at any time. We can suspend or terminate
        accounts that violate these terms. Cancelling a subscription stops
        future billing; it doesn't refund the current cycle.
      </p>
    </Section>
    <Section title="Changes" testid="terms-changes">
      <p>We may update these terms as the product evolves; continued use after a change means you accept the update.</p>
    </Section>
  </>
);

export const LegalPage = ({ doc, onBack }) => {
  const isPrivacy = doc === "privacy";
  return (
    <div className="min-h-screen flex flex-col relative overflow-hidden" data-testid={isPrivacy ? "privacy-page" : "terms-page"}>
      <AmbientBackground />
      <header className="relative z-10 max-w-3xl mx-auto w-full px-6 pt-8">
        <button
          data-testid="legal-back"
          onClick={onBack}
          className="flex items-center gap-2 text-text2 hover:text-text text-sm font-mono transition-colors"
        >
          <ArrowLeft size={14} /> capcode
        </button>
      </header>
      <main className="flex-1 relative z-10">
        <div className="max-w-3xl mx-auto px-6 py-14 w-full">
          <div className="label-xs text-canary mb-4">legal</div>
          <h1 className="font-display font-extrabold text-4xl sm:text-5xl text-text">
            {isPrivacy ? "Privacy Policy" : "Terms of Service"}
          </h1>
          <p className="mt-3 text-text3 text-xs font-mono">Last updated: {LAST_UPDATED}</p>
          {isPrivacy ? PRIVACY : TERMS}
        </div>
      </main>
      <footer className="text-text3 font-mono text-xs px-6 py-6 border-t border-line relative z-10">
        capcode
      </footer>
    </div>
  );
};
