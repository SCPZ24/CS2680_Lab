import { describe, expect, it } from "vitest";
import {
  applyDamage,
  applyHealPickup,
  createInitialPlayerState,
  tickShieldRecovery
} from "./playerState";

describe("playerState survival rules", () => {
  it("createInitialPlayerState returns expected defaults", () => {
    const p = createInitialPlayerState();

    expect(p.maxHealth).toBe(100);
    expect(p.health).toBe(100);
    expect(p.maxShield).toBe(60);
    expect(p.shield).toBe(60);
    expect(p.lastDamageAt).toBe(-Infinity);
    expect(p.shieldRecoveryDelayMs).toBe(1000);
    expect(p.shieldRecoveryPerSecond).toBe(18);
  });

  it("routes incoming damage to shield before health", () => {
    const player = createInitialPlayerState();

    const damaged = applyDamage(player, 30, 1000);

    expect(damaged.shield).toBe(30);
    expect(damaged.health).toBe(100);
    expect(damaged.lastDamageAt).toBe(1000);
  });

  it("overflow damage spills from shield to health", () => {
    const player = createInitialPlayerState();

    const damaged = applyDamage(player, 70, 1000);

    expect(damaged.shield).toBe(0);
    expect(damaged.health).toBe(90);
  });

  it("starts shield recovery only after the recovery delay", () => {
    // First take enough damage to ensure shield is below max so recovery has room.
    const player = applyDamage(createInitialPlayerState(), 50, 1000);
    expect(player.shield).toBe(10);

    const tooEarly = tickShieldRecovery(player, 1500);
    expect(tooEarly.shield).toBe(10);

    const recovered = tickShieldRecovery(player, 2500);
    expect(recovered.shield).toBeGreaterThan(10);
    expect(recovered.shield).toBeLessThanOrEqual(60);
  });

  it("shield recovery is linear across multiple ticks (no double-counting)", () => {
    const damaged = applyDamage(createInitialPlayerState(), 50, 1000);
    expect(damaged.shield).toBe(10);

    // Delay is 1000ms, so recovery starts at t=2000.
    const t1 = tickShieldRecovery(damaged, 2500);
    expect(t1.shield).toBeCloseTo(19, 6); // +0.5s * 18 = +9

    const t2 = tickShieldRecovery(t1, 3000);
    expect(t2.shield).toBeCloseTo(28, 6); // +0.5s * 18 = +9 more (not +18)
  });

  it("heals health with pickups without exceeding max health", () => {
    const player = applyDamage(createInitialPlayerState(), 90, 1000);
    expect(player.health).toBe(70);

    const healed = applyHealPickup(player, 20);
    expect(healed.health).toBe(90);

    const clamped = applyHealPickup(healed, 999);
    expect(clamped.health).toBe(100);
  });

  it("applyDamage does not update lastDamageAt when effective damage is <= 0", () => {
    const player = createInitialPlayerState();
    expect(player.lastDamageAt).toBe(-Infinity);

    const zero = applyDamage(player, 0, 1234);
    expect(zero.lastDamageAt).toBe(-Infinity);
    expect(zero.health).toBe(player.health);
    expect(zero.shield).toBe(player.shield);

    const negative = applyDamage(player, -5, 5678);
    expect(negative.lastDamageAt).toBe(-Infinity);
    expect(negative.health).toBe(player.health);
    expect(negative.shield).toBe(player.shield);
  });
});
