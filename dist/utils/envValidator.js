/**
 * Utility to validate required environment variables on startup.
 * Prevents the server from crashing mysteriously if a setting is missing.
 */
export function validateEnv() {
    const isKms = process.env.SIGNER_BACKEND === "kms";
    const requiredEnvVars = [
        "DB_URL",
        "STELLAR_KEY",
        "JWT_SECRET",
        "SESSION_SECRET",
    ];
    // If not using KMS, we need either the plaintext or encrypted secret.
    // Validation of the actual key happens in SecretManager.ts, but we check presence here.
    if (!isKms) {
        if (!process.env.STELLAR_SECRET && !process.env.ENCRYPTED_STELLAR_SECRET && !process.env.ORACLE_SECRET_KEY && !process.env.SOROBAN_ADMIN_SECRET) {
            requiredEnvVars.push("STELLAR_SECRET");
        }
        if (process.env.ENCRYPTED_STELLAR_SECRET && !process.env.VAULT_MASTER_KEY) {
            requiredEnvVars.push("VAULT_MASTER_KEY");
        }
    }
    const missingEnvVars = [];
    for (const envVar of requiredEnvVars) {
        if (!process.env[envVar]) {
            missingEnvVars.push(envVar);
        }
    }
    if (missingEnvVars.length > 0) {
        console.error("❌ [OPS] Missing required environment variables:");
        missingEnvVars.forEach((varName) => {
            console.error(`   - ${varName}`);
        });
        console.error("\nPlease set these variables in your .env file and restart the server.");
        // Exit the process with failure code
        process.exit(1);
    }
    // Log optional but recommended environment variables
    const recommendedEnvVars = [
        "MAX_LATENCY_MS",
        "REDIS_URL",
        "TRUST_PROXY",
        "JWT_EXPIRY_HOURS",
    ];
    for (const envVar of recommendedEnvVars) {
        if (!process.env[envVar]) {
            console.warn(`⚠️ [OPS] Recommended environment variable not set: ${envVar}`);
        }
        else {
            console.info(`✅ [OPS] ${envVar} = ${process.env[envVar]}`);
        }
    }
}
/**
 * Get the MAX_LATENCY_MS value from environment variables.
 * @returns The latency threshold in milliseconds, or default 30000ms (30 seconds)
 */
export function getMaxLatencyMs() {
    const envValue = process.env.MAX_LATENCY_MS;
    if (envValue === undefined || envValue === "") {
        return 30000; // Default: 30 seconds
    }
    const parsed = parseInt(envValue, 10);
    if (isNaN(parsed) || parsed <= 0) {
        console.warn(`⚠️ [OPS] Invalid MAX_LATENCY_MS value: "${envValue}". Using default 30000ms.`);
        return 30000;
    }
    return parsed;
}
//# sourceMappingURL=envValidator.js.map