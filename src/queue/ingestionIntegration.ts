/**
 * Ingestion System Integration
 * 
 * Integrates lock-free ring buffers with the existing backpressure system
 * to provide a seamless upgrade path with enhanced performance.
 */

import { BackpressureManager, IngestionPacket, PacketPriority, BackpressureConfig } from "./backpressure";
import { LockFreeIngestionChannel, LockFreeChannelManager, RingBufferConfig } from "./lockFreeRingBuffer";
import { WorkerChannelPool, WorkerThreadChannel } from "./workerThreadChannel";

/**
 * Integration configuration
 */
export interface IngestionIntegrationConfig {
  useLockFree: boolean; // Enable lock-free ring buffers
  useWorkerThreads: boolean; // Enable worker threads
  backpressureConfig?: BackpressureConfig;
  ringBufferConfig?: RingBufferConfig;
  workerConfig?: {
    bufferSize: number;
    batchSize: number;
  };
}

/**
 * Unified ingestion system that can use either traditional queues
 * or lock-free ring buffers based on configuration
 */
export class UnifiedIngestionSystem {
  private backpressureManager: BackpressureManager;
  private lockFreeChannelManager: LockFreeChannelManager;
  private workerPool: WorkerChannelPool;
  private config: IngestionIntegrationConfig;

  constructor(config: IngestionIntegrationConfig) {
    this.config = config;

    // Initialize traditional backpressure manager
    this.backpressureManager = new BackpressureManager(config.backpressureConfig);

    // Initialize lock-free channel manager
    this.lockFreeChannelManager = new LockFreeChannelManager(config.ringBufferConfig);

    // Initialize worker pool
    this.workerPool = new WorkerChannelPool(config.workerConfig);
  }

  /**
   * Enqueue a packet using the configured ingestion method
   */
  async enqueue(packet: IngestionPacket, channelName?: string): Promise<boolean> {
    if (this.config.useWorkerThreads && channelName) {
      return this.enqueueWithWorker(packet, channelName);
    } else if (this.config.useLockFree && channelName) {
      return this.enqueueLockFree(packet, channelName);
    } else {
      return this.enqueueTraditional(packet);
    }
  }

  /**
   * Enqueue using traditional backpressure manager
   */
  private async enqueueTraditional(packet: IngestionPacket): Promise<boolean> {
    return this.backpressureManager.enqueue(packet);
  }

  /**
   * Enqueue using lock-free ring buffer
   */
  private enqueueLockFree(packet: IngestionPacket, channelName: string): boolean {
    const channel = this.lockFreeChannelManager.getChannel(channelName);
    return channel.publish(packet);
  }

  /**
   * Enqueue using worker thread channel
   */
  private async enqueueWithWorker(packet: IngestionPacket, channelName: string): Promise<boolean> {
    const channel = await this.workerPool.getChannel(channelName);
    return channel.publish(packet);
  }

  /**
   * Dequeue a packet using the configured ingestion method
   */
  async dequeue(channelName?: string): Promise<IngestionPacket | undefined> {
    if (this.config.useWorkerThreads && channelName) {
      return this.dequeueFromWorker(channelName);
    } else if (this.config.useLockFree && channelName) {
      return this.dequeueLockFree(channelName);
    } else {
      return this.dequeueTraditional();
    }
  }

  /**
   * Dequeue using traditional backpressure manager
   */
  private async dequeueTraditional(): Promise<IngestionPacket | undefined> {
    return this.backpressureManager.dequeue();
  }

  /**
   * Dequeue using lock-free ring buffer
   */
  private dequeueLockFree(channelName: string): IngestionPacket | undefined {
    const channel = this.lockFreeChannelManager.getChannel(channelName);
    const packets = channel.subscribe(1);
    return packets.length > 0 ? packets[0] : undefined;
  }

  /**
   * Dequeue using worker thread channel
   */
  private async dequeueFromWorker(channelName: string): Promise<IngestionPacket | undefined> {
    const channel = await this.workerPool.getChannel(channelName);
    const packets = await channel.subscribe();
    return packets.length > 0 ? packets[0] : undefined;
  }

  /**
   * Get aggregate metrics from all ingestion systems
   */
  async getMetrics(): Promise<{
    traditional: any;
    lockFree: Map<string, any>;
    workers: Map<string, any>;
    aggregate: {
      totalSize: number;
      totalCapacity: number;
      totalEnqueued: number;
      totalDequeued: number;
      totalFailures: number;
      averageUtilization: number;
    };
  }> {
    const traditional = this.backpressureManager.getMetrics();
    const lockFree = this.lockFreeChannelManager.getAllMetrics();
    const workers = await this.workerPool.getAllMetrics();
    const aggregate = this.lockFreeChannelManager.getAggregateMetrics();

    return {
      traditional,
      lockFree,
      workers,
      aggregate,
    };
  }

  /**
   * Get queue length
   */
  getQueueLength(channelName?: string): number {
    if (this.config.useLockFree && channelName) {
      const channel = this.lockFreeChannelManager.getChannel(channelName);
      return channel.size();
    }
    return this.backpressureManager.getQueueLength();
  }

  /**
   * Check if system is under backpressure
   */
  isUnderBackpressure(): boolean {
    const metrics = this.backpressureManager.getMetrics();
    return metrics.saturation > 0.7;
  }

  /**
   * Reset all metrics
   */
  resetMetrics(): void {
    this.backpressureManager.resetMetrics();
    this.lockFreeChannelManager.resetAllMetrics();
  }

  /**
   * Shutdown the ingestion system
   */
  async shutdown(): Promise<void> {
    this.backpressureManager.shutdown();
    await this.workerPool.shutdown();
    this.lockFreeChannelManager = new LockFreeChannelManager(this.config.ringBufferConfig);
  }

  /**
   * Update configuration at runtime
   */
  updateConfig(newConfig: Partial<IngestionIntegrationConfig>): void {
    this.config = { ...this.config, ...newConfig };
  }

  /**
   * Get current configuration
   */
  getConfig(): IngestionIntegrationConfig {
    return { ...this.config };
  }
}

/**
 * Factory function to create ingestion system based on environment
 */
export function createIngestionSystem(
  config?: Partial<IngestionIntegrationConfig>
): UnifiedIngestionSystem {
  const defaultConfig: IngestionIntegrationConfig = {
    useLockFree: true,
    useWorkerThreads: false, // Disabled by default due to worker thread overhead
    backpressureConfig: {
      maxCapacity: 1000,
      dropThreshold: 0.9,
      slowDownThreshold: 0.7,
      slowDownDelay: 100,
      enableMetrics: true,
    },
    ringBufferConfig: {
      capacity: 1024,
      enableMetrics: true,
      enableBatching: true,
      batchSize: 32,
    },
    workerConfig: {
      bufferSize: 1024,
      batchSize: 32,
    },
  };

  return new UnifiedIngestionSystem({ ...defaultConfig, ...config });
}

/**
 * Singleton instance for global use
 */
let globalIngestionSystem: UnifiedIngestionSystem | null = null;

export function getGlobalIngestionSystem(): UnifiedIngestionSystem {
  if (!globalIngestionSystem) {
    globalIngestionSystem = createIngestionSystem();
  }
  return globalIngestionSystem;
}

export function setGlobalIngestionSystem(system: UnifiedIngestionSystem): void {
  globalIngestionSystem = system;
}
