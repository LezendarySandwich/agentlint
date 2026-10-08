// Who fills the first turn, as one bar in the three groups (docs/tech_impl/11-ui.md, "Who put it there"): the
// always-on outcome's own attribution, grouped by model.ts's splitOf — the function every cost row uses too. An
// observed total's remainder is a neutral segment in no group: nobody knows who put it there.
import type { Authority } from "./contract";
import { counted, GROUP_MEANING, GROUP_WORDS, short } from "./format";
import { authoritiesIn, GROUPS, type Budget, type Group } from "./model";
import { Tip } from "./ui";

/** Who is inside a group, e.g. "Harness 23.9k: harness 19.2k, runtime 4.7k, managed 77". */
export function groupWords(budget: Budget, group: Group): string {
  const inside: [string, number][] = authoritiesIn(group).map((a: Authority) => [a, budget.byAuthority[a]]);
  if (group === "harness") inside.push(["untraced text", budget.untraced]);
  const named = inside.filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]).map(([who, n]) => `${who} ${short(n)}`);
  return `${GROUP_WORDS[group]} ${short(budget.byGroup[group])}: ${named.join(", ")} — ${GROUP_MEANING[group]}`;
}

/** The bar, 8 px: a segment per group, and the remainder no source explains; a segment picks that group. */
export function BudgetSegments({ budget, onPick }: { budget: Budget; onPick: (group: Group) => void }) {
  const groups = GROUPS.filter((g) => budget.byGroup[g] > 0);
  if (budget.total === 0) return null;
  return (
    <div className="budget-bar">
      {groups.map((group) => (
        <Tip key={group} text={groupWords(budget, group)} className="segment-wrap"
             style={{ width: `${(budget.byGroup[group] / budget.total) * 100}%` }}>
          <button type="button" className={`segment g-${group}`} aria-label={`${GROUP_WORDS[group]} sources`} onClick={() => onPick(group)} />
        </Tip>
      ))}
      {budget.unattributed > 0 && (
        <Tip text={`${counted(budget.unattributed, "token", "tokens")} the session observed that no source explains: in no group`}
             className="segment-wrap" style={{ width: `${(budget.unattributed / budget.total) * 100}%` }}>
          <span className="segment unattributed" />
        </Tip>
      )}
    </div>
  );
}

/** A direct label for each group, under the bar; a label picks that group. */
export function BudgetLegend({ budget, onPick }: { budget: Budget; onPick: (group: Group) => void }) {
  const groups = GROUPS.filter((g) => budget.byGroup[g] > 0);
  if (budget.total === 0) return null;
  return (
    <div className="legend">
      {groups.map((group) => (
        <button key={group} type="button" onClick={() => onPick(group)}>
          <span className={`swatch g-${group}`} aria-hidden="true" />
          {GROUP_WORDS[group]} <span className="muted">{short(budget.byGroup[group])}</span>
        </button>
      ))}
      {budget.unattributed > 0 && <span className="muted">not attributed {short(budget.unattributed)}</span>}
    </div>
  );
}

/** The bar with its labels under it, as Configuration shows it. */
export function BudgetBar({ budget, onPick }: { budget: Budget; onPick: (group: Group) => void }) {
  if (budget.total === 0) return null;
  return (
    <div className="budget">
      <BudgetSegments budget={budget} onPick={onPick} />
      <BudgetLegend budget={budget} onPick={onPick} />
    </div>
  );
}
