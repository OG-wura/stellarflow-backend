export function randomFloat(
  min: number,
  max: number,
  decimals = 2,
) {
  return Number(
    (
      Math.random() *
        (max - min) +
      min
    ).toFixed(decimals),
  );
}

export function randomChoice<T>(
  values: T[],
): T {
  return values[
    Math.floor(
      Math.random() *
        values.length,
    )
  ];
}