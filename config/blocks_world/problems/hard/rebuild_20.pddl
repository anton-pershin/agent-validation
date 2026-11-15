(define (problem rebuild-20)
  (:domain blocks-world)
  (:objects a b c d e f - block)
  (:init
    (clear a) (clear b) (clear c) (clear d) (clear e) (hand-empty) (on a f) (on-table b) (on-table c) (on-table d) (on-table e) (on-table f))
  (:goal (and (on a c) (on b e) (on c d) (on e a) (on f b) (on-table d)))
)
