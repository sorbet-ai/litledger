/** The litledger wordmark; the dark version swaps in with the dark theme (styles.css). */
export function Brand() {
  return (
    <div className="brand">
      <img className="brand-logo light-only" src="/litledger.svg" alt="litledger" />
      <img className="brand-logo dark-only" src="/litledger-dark.svg" alt="litledger" />
    </div>
  );
}
