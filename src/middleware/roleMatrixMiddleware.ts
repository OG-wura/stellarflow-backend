import { Request, Response, NextFunction } from 'express';
import { Role, Permission } from '../types/roles.js';

export interface AuthRequest extends Request {
  user?: {
    userId: number;
    email: string;
    role: string;
    group?: string;
    permissions?: string[];
  };
}

// Tier-based Role Matrix
const ROLE_MATRIX: Record<Role, Permission[]> = {
  OBSERVER: ['read:prices', 'read:market'],
  OPERATOR: ['read:prices', 'read:market', 'write:oracle', 'read:config'],
  ADMIN: ['*'], // Full access
  SUPER_ADMIN: ['*'],
};

const SENSITIVE_PATHS = [
  '/admin',
  '/config',
  '/network',
  '/soroban',
  '/keys',
];

/**
 * Strict Group Permission Isolation Middleware
 */
export const enforceRoleMatrix = (requiredPermission?: Permission) => {
  return (req: AuthRequest, res: Response, next: NextFunction) => {
    const user = req.user;

    if (!user) {
      return res.status(401).json({
        error: 'Unauthorized',
        message: 'Valid authentication required'
      });
    }

    // Early blocking for sensitive paths
    const isSensitivePath = SENSITIVE_PATHS.some(path => 
      req.path.toLowerCase().startsWith(path)
    );

    if (isSensitivePath && user.role === ('OBSERVER' as Role)) {
      return res.status(403).json({
        error: 'Access Denied',
        message: 'Observer keys cannot access administrative or configuration endpoints',
        code: 'ROLE_ISOLATION_VIOLATION'
      });
    }

    // Permission check
    const userPermissions = ROLE_MATRIX[user.role as Role] || [];

    if (requiredPermission && 
        !userPermissions.includes(requiredPermission) && 
        !userPermissions.includes('*')) {
      return res.status(403).json({
        error: 'Insufficient Permissions',
        message: `Role ${user.role} lacks permission: ${requiredPermission}`,
        code: 'INSUFFICIENT_PERMISSIONS'
      });
    }

    next();
  };
};

// Convenience middleware
export const requireAdmin = enforceRoleMatrix();
export const requireOperator = enforceRoleMatrix('write:oracle');