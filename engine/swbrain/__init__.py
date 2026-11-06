"""swbrain — 阿布扎比「会思考的排水系统」。

分层与上海《控-监-测》任务书的对应：
  测：源库里的资产台账与管网图（只读）        → extract
  监：水力状态、汇水区状态、片区状态          → graph / hydraulics / indicators
  控：在什么时间、什么位置、加或调什么设施    → decisions
输出：本地 PostGIS（sw_brain）、GeoJSON、控制台数据     → export
"""
__version__ = "0.1.0"
