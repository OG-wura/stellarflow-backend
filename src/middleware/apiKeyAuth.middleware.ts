import { Request, Response, NextFunction } from "express";

/**
 * API Key Authentication Middleware
 * Validates requests using x-api-key header against the configured API key.
 */
export function apiKeyAuth() {
  return (req: Request, res: Response, next: NextFunction): void => {
    const apiKey = req.headers["x-api-key"];
    const configuredKey = process.env.CACHE_API_KEY;

    if (!configuredKey) {
      // No key configured — open access (dev mode)
      next();
      return;
    }

    if (!apiKey || apiKey !== configuredKey) {
      res.status(401).json({
        success: false,
        error: "Unauthorized: invalid or missing API key",
      });
      return;
    }

    next();
  };
}
