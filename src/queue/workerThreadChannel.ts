/**
 * Worker Thread Channel for Multi-Threaded Ingestion
 * 
 * Integrates lock-free ring buffers with Node.js worker threads
 * for true multi-threaded ingestion without event loop blocking.
 */

import { Worker, isMainThread, parentPort, workerData } from "worker_threads";
import { LockFreeIngestionChannel, LockFreeChannelManager } from "./lockFreeRingBuffer";
import { IngestionPacket, PacketPriority } from "./backpressure";

/**
 * Worker thread configuration
 */
export interface WorkerChannelConfig {
  channelName: string;
  bufferSize: number;
  batchSize: number;
  workerScript?: string;
}

/**
 * Message types for worker communication
 */
enum WorkerMessageType {
  PUBLISH = "publish",
  SUBSCRIBE = "subscribe",
  METRICS = "metrics",
  SHUTDOWN = "shutdown",
  ACK = "ack",
  ERROR = "error",
}

interface WorkerMessage {
  type: WorkerMessageType;
  data?: any;
  id?: string;
}

/**
 * Worker-side channel handler
 * Runs inside a worker thread to handle ingestion without blocking main thread
 */
class WorkerChannelHandler {
  private channel: LockFreeIngestionChannel;
  private running: boolean = true;

  constructor(config: WorkerChannelConfig) {
    this.channel = new LockFreeIngestionChannel(config.channelName, {
      capacity: config.bufferSize,
      batchSize: config.batchSize,
      enableMetrics: true,
      enableBatching: true,
    });

    this.setupMessageHandler();
  }

  private setupMessageHandler(): void {
    if (!parentPort) return;

    parentPort.on("message", (message: WorkerMessage) => {
      if (!this.running) return;

      switch (message.type) {
        case WorkerMessageType.PUBLISH:
          this.handlePublish(message.data);
          break;
        case WorkerMessageType.SUBSCRIBE:
          this.handleSubscribe(message.id);
          break;
        case WorkerMessageType.METRICS:
          this.handleMetrics(message.id);
          break;
        case WorkerMessageType.SHUTDOWN:
          this.handleShutdown();
          break;
      }
    });
  }

  private handlePublish(packet: IngestionPacket): void {
    const success = this.channel.publish(packet);
    this.sendAck(success);
  }

  private handleSubscribe(requestId?: string): void {
    const packets = this.channel.subscribe();
    this.sendResponse(WorkerMessageType.ACK, packets, requestId);
  }

  private handleMetrics(requestId?: string): void {
    const metrics = this.channel.getMetrics();
    this.sendResponse(WorkerMessageType.ACK, metrics, requestId);
  }

  private handleShutdown(): void {
    this.running = false;
    this.sendAck(true);
  }

  private sendAck(success: boolean): void {
    if (!parentPort) return;
    parentPort.postMessage({
      type: WorkerMessageType.ACK,
      data: { success },
    });
  }

  private sendResponse(type: WorkerMessageType, data: any, id?: string): void {
    if (!parentPort) return;
    parentPort.postMessage({
      type,
      data,
      id,
    });
  }
}

/**
 * Main-thread channel client
 * Manages worker threads and provides async interface for ingestion
 */
export class WorkerThreadChannel {
  private worker: Worker | null = null;
  private config: WorkerChannelConfig;
  private messageId: number = 0;
  private pendingRequests: Map<string, (data: any) => void> = new Map();
  private running: boolean = false;

  constructor(config: WorkerChannelConfig) {
    this.config = config;
  }

  /**
   * Start the worker thread
   */
  async start(): Promise<void> {
    if (this.running) return;

    try {
      const workerScript = this.config.workerScript || 
        __filename.replace(/\.ts$/, ".js");

      this.worker = new Worker(workerScript, {
        workerData: this.config,
      });

      this.worker.on("message", (message: WorkerMessage) => {
        this.handleWorkerMessage(message);
      });

      this.worker.on("error", (error) => {
        console.error(`[WorkerChannel] Worker error:`, error);
      });

      this.worker.on("exit", (code) => {
        if (code !== 0) {
          console.error(`[WorkerChannel] Worker stopped with exit code ${code}`);
        }
        this.running = false;
      });

      this.running = true;
      console.log(`[WorkerChannel] Started worker for channel ${this.config.channelName}`);
    } catch (error) {
      console.error(`[WorkerChannel] Failed to start worker:`, error);
      throw error;
    }
  }

  /**
   * Publish a packet to the channel (non-blocking)
   */
  async publish(packet: IngestionPacket): Promise<boolean> {
    if (!this.running || !this.worker) {
      console.warn("[WorkerChannel] Worker not running, publishing directly");
      return false;
    }

    return new Promise((resolve) => {
      const id = this.generateMessageId();
      this.pendingRequests.set(id, resolve);

      this.worker!.postMessage({
        type: WorkerMessageType.PUBLISH,
        data: packet,
        id,
      });

      // Timeout after 5 seconds
      setTimeout(() => {
        if (this.pendingRequests.has(id)) {
          this.pendingRequests.delete(id);
          resolve(false);
        }
      }, 5000);
    });
  }

  /**
   * Subscribe to receive packets from the channel
   */
  async subscribe(): Promise<IngestionPacket[]> {
    if (!this.running || !this.worker) {
      return [];
    }

    return new Promise((resolve) => {
      const id = this.generateMessageId();
      this.pendingRequests.set(id, resolve);

      this.worker!.postMessage({
        type: WorkerMessageType.SUBSCRIBE,
        id,
      });

      // Timeout after 5 seconds
      setTimeout(() => {
        if (this.pendingRequests.has(id)) {
          this.pendingRequests.delete(id);
          resolve([]);
        }
      }, 5000);
    });
  }

  /**
   * Get channel metrics
   */
  async getMetrics(): Promise<any> {
    if (!this.running || !this.worker) {
      return null;
    }

    return new Promise((resolve) => {
      const id = this.generateMessageId();
      this.pendingRequests.set(id, resolve);

      this.worker!.postMessage({
        type: WorkerMessageType.METRICS,
        id,
      });

      // Timeout after 5 seconds
      setTimeout(() => {
        if (this.pendingRequests.has(id)) {
          this.pendingRequests.delete(id);
          resolve(null);
        }
      }, 5000);
    });
  }

  /**
   * Stop the worker thread
   */
  async stop(): Promise<void> {
    if (!this.running || !this.worker) return;

    this.worker.postMessage({
      type: WorkerMessageType.SHUTDOWN,
    });

    await this.worker.terminate();
    this.worker = null;
    this.running = false;
    this.pendingRequests.clear();

    console.log(`[WorkerChannel] Stopped worker for channel ${this.config.channelName}`);
  }

  /**
   * Check if worker is running
   */
  isActive(): boolean {
    return this.running && this.worker !== null;
  }

  private handleWorkerMessage(message: WorkerMessage): void {
    if (message.id && this.pendingRequests.has(message.id)) {
      const resolve = this.pendingRequests.get(message.id)!;
      this.pendingRequests.delete(message.id);
      resolve(message.data);
    }
  }

  private generateMessageId(): string {
    return `msg_${this.messageId++}_${Date.now()}`;
  }
}

/**
 * Pool of worker thread channels for parallel ingestion
 */
export class WorkerChannelPool {
  private channels: Map<string, WorkerThreadChannel> = new Map();
  private defaultConfig: Partial<WorkerChannelConfig>;

  constructor(defaultConfig?: Partial<WorkerChannelConfig>) {
    this.defaultConfig = {
      bufferSize: 1024,
      batchSize: 32,
      ...defaultConfig,
    };
  }

  /**
   * Get or create a worker channel
   */
  async getChannel(name: string, config?: Partial<WorkerChannelConfig>): Promise<WorkerThreadChannel> {
    let channel = this.channels.get(name);
    if (!channel) {
      channel = new WorkerThreadChannel({
        channelName: name,
        ...this.defaultConfig,
        ...config,
      });
      await channel.start();
      this.channels.set(name, channel);
    }
    return channel;
  }

  /**
   * Remove a channel
   */
  async removeChannel(name: string): Promise<void> {
    const channel = this.channels.get(name);
    if (channel) {
      await channel.stop();
      this.channels.delete(name);
    }
  }

  /**
   * Get all channel metrics
   */
  async getAllMetrics(): Promise<Map<string, any>> {
    const metrics = new Map<string, any>();
    for (const [name, channel] of this.channels) {
      if (channel.isActive()) {
        metrics.set(name, await channel.getMetrics());
      }
    }
    return metrics;
  }

  /**
   * Shutdown all channels
   */
  async shutdown(): Promise<void> {
    const shutdownPromises = Array.from(this.channels.values()).map(
      channel => channel.stop()
    );
    await Promise.all(shutdownPromises);
    this.channels.clear();
  }

  /**
   * Get active channel count
   */
  getActiveCount(): number {
    let count = 0;
    for (const channel of this.channels.values()) {
      if (channel.isActive()) count++;
    }
    return count;
  }
}

// Initialize worker if running in worker thread
if (!isMainThread && workerData) {
  new WorkerChannelHandler(workerData as WorkerChannelConfig);
}
